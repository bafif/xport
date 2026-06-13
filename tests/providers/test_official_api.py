from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.providers.base import ProviderError, SearchQuery
from tweet_extractor.providers.official_api import (
    V2_MAX_RESULTS,
    OfficialApiProvider,
    build_params,
    count_accessed_v2,
    extract_next_token,
    extract_tweets_v2,
    fetch_user_tweets_page,
)

QUERY = SearchQuery(
    username="someuser",
    since=datetime(2023, 1, 1, tzinfo=UTC),
    until=datetime(2023, 1, 8, tzinfo=UTC),
)


def v2_response(
    tweet_ids: list[str], *, includes: list[str] | None = None, next_token: str | None = None
) -> dict[str, Any]:
    """Una respuesta de `/2/users/:id/tweets`: `data[]` (la página), `includes.tweets[]`
    (referenciados hidratados) y `meta.next_token`."""
    raw: dict[str, Any] = {
        "data": [{"id": tid, "text": f"texto {tid}"} for tid in tweet_ids],
        "meta": {"result_count": len(tweet_ids)},
    }
    if includes is not None:
        raw["includes"] = {"tweets": [{"id": tid, "text": f"ref {tid}"} for tid in includes]}
    if next_token is not None:
        raw["meta"]["next_token"] = next_token
    return raw


# --- helpers de envelope (forma v2 documentada) ---


def test_extract_tweets_v2_devuelve_data():
    raw = v2_response(["1", "2"])
    assert [t["id"] for t in extract_tweets_v2(raw)] == ["1", "2"]


def test_extract_tweets_v2_pagina_vacia():
    assert extract_tweets_v2({"meta": {"result_count": 0}}) == []


def test_extract_next_token():
    assert extract_next_token(v2_response(["1"], next_token="NEXT")) == "NEXT"


def test_extract_next_token_none_al_final():
    assert extract_next_token(v2_response(["1"])) is None


def test_count_accessed_solo_data():
    assert count_accessed_v2(v2_response(["1", "2"])) == 2


def test_count_accessed_sobre_cuenta_includes():
    # Los referenciados (quote/RT) hidratados en includes también contaron como acceso.
    raw = v2_response(["1"], includes=["900", "901"])
    assert count_accessed_v2(raw) == 3  # 1 de data + 2 de includes


def test_count_accessed_nunca_menor_que_data():
    raw = v2_response(["1", "2", "3"])
    assert count_accessed_v2(raw) >= len(extract_tweets_v2(raw))


# --- build_params (request v2 documentado) ---


def test_build_params_primera_pagina_sin_token():
    params = build_params("2023-01-01", "2023-01-08", 100, None)
    assert params["start_time"] == "2023-01-01"
    assert params["end_time"] == "2023-01-08"
    assert params["max_results"] == 100
    assert "pagination_token" not in params
    assert "referenced_tweets" in params["tweet.fields"]


def test_build_params_incluye_token_si_hay():
    params = build_params("2023-01-01", "2023-01-08", 100, "TOK")
    assert params["pagination_token"] == "TOK"


# --- el provider (stub por defecto, real con fetcher inyectado) ---


async def test_fetch_page_falla_cerrado_por_defecto():
    # Sin fetcher real inyectado: el backend oficial es un stub -> ProviderError.
    provider = OfficialApiProvider(Settings())
    with pytest.raises(ProviderError):
        await provider.fetch_page(QUERY, None)


async def test_default_fetcher_levanta_provider_error():
    with pytest.raises(ProviderError):
        await fetch_user_tweets_page(Settings(), QUERY, None)


async def test_fetch_page_con_fetcher_inyectado_parsea_envelope():
    async def fake(settings: Settings, query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        return v2_response(["1", "2"], includes=["900"], next_token="N")

    provider = OfficialApiProvider(Settings(), page_fetcher=fake)
    page = await provider.fetch_page(QUERY, None)
    assert [t["id"] for t in page.tweets] == ["1", "2"]
    assert page.accessed_count == 3  # data + includes
    assert page.next_cursor == "N"


async def test_fetch_tweets_pagina_con_fetcher_inyectado():
    paginas = {
        None: v2_response(["1"], next_token="c1"),
        "c1": v2_response(["2"]),  # sin next_token -> corta
    }

    async def fake(settings: Settings, query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        return paginas[cursor]

    provider = OfficialApiProvider(Settings(), page_fetcher=fake)
    ids = [t["id"] async for t in provider.fetch_tweets(QUERY)]
    assert ids == ["1", "2"]


def test_cota_de_reserva_sobre_estima():
    provider = OfficialApiProvider(Settings())
    # La cota del gate = página completa (≤100) * factor de quotes embebidos.
    assert provider.max_accessed_per_page == V2_MAX_RESULTS * Settings().ACCESS_FACTOR_PER_TWEET
    assert provider.max_accessed_per_page > V2_MAX_RESULTS


async def test_gated_provider_envuelve_al_oficial():
    # El gate es agnóstico del backend: debe envolver al oficial igual que al de scraping.
    async def fake(settings: Settings, query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        return v2_response(["1", "2"])

    provider = OfficialApiProvider(Settings(), page_fetcher=fake)
    async with SlidingWindowGate(":memory:") as gate:
        gated = GatedProvider(provider, gate)
        page = await gated.fetch_page(QUERY, None)
        assert [t["id"] for t in page.tweets] == ["1", "2"]
        # Reserva la cota; reconcilia al real (data + includes).
        assert await gate.usage() == 2
