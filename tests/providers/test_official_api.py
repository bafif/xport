from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.providers.base import ProviderError, SearchQuery
from tweet_extractor.providers.official_api import (
    MAX_RATE_LIMIT_WAITS,
    SEARCH_ALL_ENDPOINT,
    V2_MAX_RESULTS,
    OfficialApiProvider,
    SearchAllFetcher,
    build_params,
    count_accessed_v2,
    extract_next_token,
    extract_tweets_v2,
)

# Respuestas fabricadas con la forma que documenta X: no hay una clave paga con qué
# compararlas contra respuestas reales.

QUERY = SearchQuery(
    username="someuser",
    since=datetime(2023, 1, 1, tzinfo=UTC),
    until=datetime(2023, 1, 8, tzinfo=UTC),
)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
TOKEN = Settings(x_api_bearer_token="tok")


def v2_response(
    tweet_ids: list[str], *, includes: list[str] | None = None, next_token: str | None = None
) -> dict[str, Any]:
    """Una página de `/2/tweets/search/all`: `data[]`, `includes.tweets[]` (si se
    hubieran pedido expansions) y `meta.next_token`."""
    raw: dict[str, Any] = {
        "data": [{"id": tid, "text": f"texto {tid}"} for tid in tweet_ids],
        "meta": {"result_count": len(tweet_ids)},
    }
    if includes is not None:
        raw["includes"] = {"tweets": [{"id": tid, "text": f"ref {tid}"} for tid in includes]}
    if next_token is not None:
        raw["meta"]["next_token"] = next_token
    return raw


class FakeTime:
    """Reloj y sleep falsos: `sleep` avanza el reloj y anota cuánto se esperó."""

    def __init__(self, start: float = NOW.timestamp()) -> None:
        self.now = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def fetcher_with(
    responses: list[httpx.Response], seen: list[httpx.Request], fake: FakeTime
) -> SearchAllFetcher:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return SearchAllFetcher(
        TOKEN, transport=httpx.MockTransport(handler), sleep=fake.sleep, clock=fake.clock
    )


# --- helpers de envelope ---


def test_extract_tweets_v2_devuelve_data():
    assert [t["id"] for t in extract_tweets_v2(v2_response(["1", "2"]))] == ["1", "2"]


def test_extract_tweets_v2_pagina_vacia():
    assert extract_tweets_v2({"meta": {"result_count": 0}}) == []


def test_extract_next_token():
    assert extract_next_token(v2_response(["1"], next_token="NEXT")) == "NEXT"
    assert extract_next_token(v2_response(["1"])) is None


def test_count_accessed_suma_includes_con_los_dos_nombres():
    # Las docs de X llaman `tweets` o `posts` a los referenciados según la página.
    raw = v2_response(["1"], includes=["900", "901"])
    raw["includes"]["posts"] = [{"id": "902"}]
    assert count_accessed_v2(raw) == 4


# --- build_params ---


def test_build_params_pide_el_rango_sin_retweets_ni_expansions():
    params = build_params(QUERY, max_results=500, next_token=None, now=NOW)
    assert params is not None
    assert params["query"] == "from:someuser -is:retweet"
    assert params["start_time"] == "2023-01-01T00:00:00Z"
    assert params["end_time"] == "2023-01-08T00:00:00Z"
    assert params["max_results"] == 500
    assert "referenced_tweets" in params["tweet.fields"]
    assert "note_tweet" in params["tweet.fields"]
    assert "expansions" not in params  # X las cobra desde el 22/11/2026
    assert "next_token" not in params


def test_build_params_incluye_el_token_de_pagina():
    params = build_params(QUERY, max_results=500, next_token="TOK", now=NOW)
    assert params is not None and params["next_token"] == "TOK"


def test_build_params_recorta_end_time_antes_de_ahora():
    query = SearchQuery(
        username="u",
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=datetime(2026, 11, 8, tzinfo=UTC),
    )
    params = build_params(query, max_results=500, next_token=None, now=NOW)
    assert params is not None
    assert params["end_time"] == "2026-11-06T11:59:30Z"


def test_build_params_tramo_en_el_futuro_no_pide_nada():
    query = SearchQuery(
        username="u",
        since=datetime(2026, 11, 7, tzinfo=UTC),
        until=datetime(2026, 11, 8, tzinfo=UTC),
    )
    assert build_params(query, max_results=500, next_token=None, now=NOW) is None


# --- SearchAllFetcher (red simulada) ---


def test_sin_token_falla_al_armarlo():
    with pytest.raises(ProviderError, match="X_API_BEARER_TOKEN"):
        SearchAllFetcher(Settings(x_api_bearer_token=None))


async def test_fetcher_manda_el_token_y_los_params():
    seen: list[httpx.Request] = []
    fake = FakeTime()
    fetch = fetcher_with([httpx.Response(200, json=v2_response(["1"]))], seen, fake)

    raw = await fetch(QUERY, "TOK")

    assert extract_tweets_v2(raw)[0]["id"] == "1"
    (req,) = seen
    assert str(req.url).startswith(SEARCH_ALL_ENDPOINT)
    assert req.headers["authorization"] == "Bearer tok"
    assert req.url.params["query"] == "from:someuser -is:retweet"
    assert req.url.params["next_token"] == "TOK"


async def test_fetcher_tramo_en_el_futuro_no_llama_a_la_api():
    seen: list[httpx.Request] = []
    fake = FakeTime()
    fetch = fetcher_with([], seen, fake)
    query = SearchQuery(
        username="u",
        since=datetime(2026, 11, 7, tzinfo=UTC),
        until=datetime(2026, 11, 8, tzinfo=UTC),
    )
    assert extract_tweets_v2(await fetch(query, None)) == []
    assert seen == []


async def test_fetcher_espera_hasta_el_reset_ante_429():
    seen: list[httpx.Request] = []
    fake = FakeTime()
    reset = str(int(fake.now) + 120)
    fetch = fetcher_with(
        [
            httpx.Response(429, headers={"x-rate-limit-reset": reset}, json={"title": "Too Many"}),
            httpx.Response(200, json=v2_response(["1"])),
        ],
        seen,
        fake,
    )

    raw = await fetch(QUERY, None)

    assert extract_tweets_v2(raw)[0]["id"] == "1"
    assert len(seen) == 2
    assert 120 <= max(fake.slept) <= 122  # hasta el reset (+1 s), no un número fijo


async def test_fetcher_se_rinde_tras_muchos_429():
    seen: list[httpx.Request] = []
    fake = FakeTime()
    fetch = fetcher_with([httpx.Response(429)] * (MAX_RATE_LIMIT_WAITS + 1), seen, fake)
    with pytest.raises(ProviderError, match="429"):
        await fetch(QUERY, None)


async def test_fetcher_reintenta_un_5xx():
    seen: list[httpx.Request] = []
    fake = FakeTime()
    fetch = fetcher_with(
        [httpx.Response(503), httpx.Response(200, json=v2_response(["1"]))], seen, fake
    )
    assert extract_tweets_v2(await fetch(QUERY, None))[0]["id"] == "1"
    assert len(seen) == 2


@pytest.mark.parametrize(
    ("status", "pista"),
    [(401, "no es válido"), (402, "crédito"), (403, "archivo completo")],
)
async def test_fetcher_explica_los_errores_del_plan_o_del_token(status: int, pista: str):
    seen: list[httpx.Request] = []
    fake = FakeTime()
    fetch = fetcher_with([httpx.Response(status, json={"title": "Forbidden"})], seen, fake)
    with pytest.raises(ProviderError, match=pista):
        await fetch(QUERY, None)
    assert len(seen) == 1  # no reintenta lo que no se arregla esperando


async def test_fetcher_deja_un_segundo_entre_pedidos():
    seen: list[httpx.Request] = []
    fake = FakeTime()
    fetch = fetcher_with(
        [
            httpx.Response(200, json=v2_response(["1"])),
            httpx.Response(200, json=v2_response(["2"])),
        ],
        seen,
        fake,
    )
    await fetch(QUERY, None)
    await fetch(QUERY, "c1")
    assert fake.slept == [1.0]


# --- el provider ---


def test_provider_sin_token_falla_al_armarlo():
    with pytest.raises(ProviderError):
        OfficialApiProvider(Settings(x_api_bearer_token=None))


async def test_fetch_page_parsea_envelope():
    async def fake(query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        return v2_response(["1", "2"], includes=["900"], next_token="N")

    provider = OfficialApiProvider(TOKEN, page_fetcher=fake)
    page = await provider.fetch_page(QUERY, None)
    assert [t["id"] for t in page.tweets] == ["1", "2"]
    assert page.accessed_count == 3  # data + includes
    assert page.next_cursor == "N"


async def test_fetch_tweets_pagina():
    paginas = {None: v2_response(["1"], next_token="c1"), "c1": v2_response(["2"])}

    async def fake(query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        return paginas[cursor]

    provider = OfficialApiProvider(TOKEN, page_fetcher=fake)
    assert [t["id"] async for t in provider.fetch_tweets(QUERY)] == ["1", "2"]


def test_cota_de_reserva_sobre_estima():
    provider = OfficialApiProvider(TOKEN)
    assert provider.max_accessed_per_page == V2_MAX_RESULTS * Settings.ACCESS_FACTOR_PER_TWEET
    assert provider.max_accessed_per_page > V2_MAX_RESULTS


async def test_gated_provider_envuelve_al_oficial():
    # El gate es agnóstico del backend: envuelve al oficial igual que al de scraping.
    async def fake(query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        return v2_response(["1", "2"])

    provider = OfficialApiProvider(TOKEN, page_fetcher=fake)
    async with SlidingWindowGate(":memory:") as gate:
        gated = GatedProvider(provider, gate)
        page = await gated.fetch_page(QUERY, None)
        assert [t["id"] for t in page.tweets] == ["1", "2"]
        assert await gate.usage() == 2  # reserva la cota y reconcilia al real
