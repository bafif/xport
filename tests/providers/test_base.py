from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tweet_extractor.providers.base import Page, SearchQuery


async def test_fetch_tweets_pagina_y_para_en_next_cursor_none(make_provider, sample_query):
    pages = [
        Page(tweets=[{"id": "1"}, {"id": "2"}], accessed_count=2, next_cursor="p1"),
        Page(tweets=[{"id": "3"}], accessed_count=1, next_cursor=None),
    ]
    provider = make_provider(pages)

    out = [t async for t in provider.fetch_tweets(sample_query)]

    assert [t["id"] for t in out] == ["1", "2", "3"]
    assert provider.cursors_seen == [None, "p1"]


async def test_fetch_tweets_una_sola_pagina(make_provider, sample_query):
    pages = [Page(tweets=[{"id": "1"}], accessed_count=1, next_cursor=None)]
    provider = make_provider(pages)
    out = [t async for t in provider.fetch_tweets(sample_query)]
    assert out == [{"id": "1"}]
    assert provider.cursors_seen == [None]


async def test_fetch_tweets_pagina_vacia_con_cursor_continua(make_provider, sample_query):
    pages = [
        Page(tweets=[], accessed_count=3, next_cursor="p1"),
        Page(tweets=[{"id": "1"}], accessed_count=1, next_cursor=None),
    ]
    provider = make_provider(pages)
    out = [t async for t in provider.fetch_tweets(sample_query)]
    assert out == [{"id": "1"}]
    assert provider.cursors_seen == [None, "p1"]


def test_searchquery_rechaza_datetime_naive():
    with pytest.raises(ValueError):
        SearchQuery(username="u", since=datetime(2023, 1, 1), until=datetime(2024, 1, 1))


def test_searchquery_rechaza_since_posterior_o_igual_a_until():
    with pytest.raises(ValueError):
        SearchQuery(
            username="u",
            since=datetime(2024, 1, 1, tzinfo=UTC),
            until=datetime(2023, 1, 1, tzinfo=UTC),
        )
