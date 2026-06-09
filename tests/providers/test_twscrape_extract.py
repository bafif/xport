from __future__ import annotations

from tests.providers._fixtures import (
    cursor_entry,
    search_response,
    tweet_entry,
    tweet_result,
)
from tweet_extractor.providers.twscrape_provider import (
    count_accessed,
    extract_bottom_cursor,
    extract_tweet_results,
)


def test_extract_tweet_results_devuelve_los_de_nivel_tope():
    raw = search_response([tweet_entry("1"), tweet_entry("2"), cursor_entry("CUR")])
    out = extract_tweet_results(raw)
    assert [t["rest_id"] for t in out] == ["1", "2"]


def test_extract_tweet_results_ignora_quotes_y_rt_embebidos():
    # El quote/RT viven DENTRO de result (no son entries) -> no son nivel-tope.
    raw = search_response(
        [
            tweet_entry("1", quoted=tweet_result("q1")),
            tweet_entry("2", retweeted=tweet_result("rt2")),
        ]
    )
    out = extract_tweet_results(raw)
    assert [t["rest_id"] for t in out] == ["1", "2"]


def test_extract_tweet_results_no_desenvuelve_tweet_with_visibility_results():
    # El provider entrega el wrapper crudo; desenvolver `.tweet` es del mapper.
    raw = search_response([tweet_entry("1", typename="TweetWithVisibilityResults")])
    out = extract_tweet_results(raw)
    assert len(out) == 1
    assert out[0]["__typename"] == "TweetWithVisibilityResults"
    assert out[0]["tweet"]["rest_id"] == "1"


def test_extract_tweet_results_pagina_vacia():
    assert extract_tweet_results(search_response([])) == []


def test_extract_bottom_cursor_devuelve_value():
    raw = search_response(
        [tweet_entry("1"), cursor_entry("NEXT"), cursor_entry("PREV", kind="Top")]
    )
    assert extract_bottom_cursor(raw) == "NEXT"


def test_extract_bottom_cursor_none_al_final():
    raw = search_response([tweet_entry("1")])
    assert extract_bottom_cursor(raw) is None


def test_count_accessed_cuenta_citante():
    raw = search_response([tweet_entry("1"), tweet_entry("2")])
    assert count_accessed(raw) == 2


def test_count_accessed_sobre_cuenta_quote_embebido():
    # Compliance: el quote embebido cuenta como acceso aunque no se persista.
    raw = search_response([tweet_entry("1", quoted=tweet_result("q1"))])
    assert count_accessed(raw) == 2  # citante + quote


def test_count_accessed_sobre_cuenta_rt_descartado():
    raw = search_response([tweet_entry("1", retweeted=tweet_result("rt1"))])
    assert count_accessed(raw) == 2  # citante + RT (que el mapper descartará)


def test_count_accessed_tweet_with_visibility_results_cuenta_dos():
    # Wrapper (TVR) + .tweet interno (Tweet) -> 2: sobre-cuenta, dirección segura del cap.
    raw = search_response([tweet_entry("1", typename="TweetWithVisibilityResults")])
    assert count_accessed(raw) == 2


def test_count_accessed_nunca_menor_que_tweets_de_nivel_tope():
    raw = search_response([tweet_entry("1", quoted=tweet_result("q1")), tweet_entry("2")])
    assert count_accessed(raw) >= len(extract_tweet_results(raw))
