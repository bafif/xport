from __future__ import annotations

from typing import Any

from tests.providers._fixtures import cursor_entry, search_response, tweet_entry
from tweet_extractor.config import Settings
from tweet_extractor.providers.base import SearchQuery
from tweet_extractor.providers.twscrape_provider import TwscrapeProvider


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


async def _unused_fetcher(
    pool: Any, query_str: str, count: int, cursor: str | None
) -> dict[str, Any]:
    return search_response([])
