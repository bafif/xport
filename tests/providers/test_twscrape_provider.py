from __future__ import annotations

from typing import Any

import pytest

from tests.providers._fixtures import cursor_entry, search_response, tweet_entry, tweet_result
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.providers._twscrape_gql import _build_params
from tweet_extractor.providers.base import ProviderError, SearchQuery
from tweet_extractor.providers.twscrape_provider import TwscrapeProvider, build_pool


def _settings() -> Settings:
    return Settings(_env_file=None)


async def test_fetch_page_arma_page_desde_la_respuesta(sample_query: SearchQuery) -> None:
    resp = search_response([tweet_entry("1"), tweet_entry("2"), cursor_entry("CUR")])

    async def fake_fetch(
        pool: Any, query_str: str, count: int, cursor: str | None
    ) -> dict[str, Any]:
        return resp

    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=fake_fetch)
    page = await provider.fetch_page(sample_query, None)

    assert [t["rest_id"] for t in page.tweets] == ["1", "2"]
    assert page.next_cursor == "CUR"
    assert page.accessed_count == 2


async def test_fetch_page_pasa_query_count_y_cursor_al_fetcher(sample_query: SearchQuery) -> None:
    seen: dict[str, Any] = {}

    async def fake_fetch(
        pool: Any, query_str: str, count: int, cursor: str | None
    ) -> dict[str, Any]:
        seen["query_str"] = query_str
        seen["count"] = count
        seen["cursor"] = cursor
        return search_response([])

    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=fake_fetch)
    await provider.fetch_page(sample_query, "CUR")

    assert seen["query_str"] == "from:someuser since:2023-01-01 until:2024-01-01"
    assert seen["count"] == 20
    assert seen["cursor"] == "CUR"


async def test_fetch_page_expone_la_cota_de_reserva() -> None:
    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=_unused_fetcher)
    assert provider.max_accessed_per_page == 60  # page_size(20) * ACCESS_FACTOR(3)


async def test_gated_twscrape_reserva_reconcilia_y_pagina(tmp_path, make_gate, sample_query):
    page1 = search_response(
        [
            tweet_entry("1", quoted=tweet_result("q1")),  # citante(1) + quote(1) = 2 accedidos
            cursor_entry("C1"),
        ]
    )
    page2 = search_response([])  # sin entries ni cursor -> fin de la paginación
    responses: dict[str | None, dict[str, Any]] = {None: page1, "C1": page2}

    async def fake_fetch(
        pool: Any, query_str: str, count: int, cursor: str | None
    ) -> dict[str, Any]:
        return responses[cursor]

    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=fake_fetch)
    gate = make_gate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()
    gated = GatedProvider(provider, gate)

    out = [t async for t in gated.fetch_tweets(sample_query)]

    assert [t["rest_id"] for t in out] == ["1"]
    # page1 reconcilia a 2; page2 a 0. La cota reservada (60) NO queda en el ledger.
    assert await gate.usage() == 2


async def _unused_fetcher(
    pool: Any, query_str: str, count: int, cursor: str | None
) -> dict[str, Any]:
    return search_response([])


def test_build_params_incluye_rawquery_count_product():
    params = _build_params("from:u since:2023-01-01 until:2023-01-08", 20, None)
    variables = params["variables"]
    assert variables["rawQuery"] == "from:u since:2023-01-01 until:2023-01-08"
    assert variables["count"] == 20
    assert variables["product"] == "Latest"
    assert "cursor" not in variables  # primera página sin cursor


def test_build_params_agrega_cursor_si_hay():
    params = _build_params("from:u since:2023-01-01 until:2023-01-08", 20, "CUR")
    assert params["variables"]["cursor"] == "CUR"


async def test_build_pool_carga_cuenta_activa_con_cookies(tmp_path):
    settings = Settings(
        _env_file=None,
        x_auth_token="AUTHTOKEN",
        x_ct0="CT0TOKEN",
        accounts_db_path=tmp_path / "accounts.db",
    )
    pool = await build_pool(settings)
    acc = await pool.get_account("xport-session")
    assert acc is not None
    assert acc.active is True


async def test_build_pool_idempotente(tmp_path):
    settings = Settings(
        _env_file=None,
        x_auth_token="AUTHTOKEN",
        x_ct0="CT0TOKEN",
        accounts_db_path=tmp_path / "accounts.db",
    )
    await build_pool(settings)
    pool = await build_pool(settings)  # segunda vez: no debe crashear ni duplicar
    acc = await pool.get_account("xport-session")
    assert acc is not None


async def test_build_pool_sin_cookies_falla(tmp_path):
    settings = Settings(_env_file=None, accounts_db_path=tmp_path / "accounts.db")
    with pytest.raises(ProviderError):
        await build_pool(settings)
