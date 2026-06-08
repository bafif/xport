from __future__ import annotations

import pytest
from pydantic import ValidationError

from tweet_extractor.domain.models import TweetLink


def test_tweetlink_valido_con_y_sin_display_url():
    sin = TweetLink(url="https://t.co/abc", expanded_url="https://ejemplo.com/doc")
    assert sin.display_url is None

    con = TweetLink(
        url="https://t.co/abc",
        expanded_url="https://ejemplo.com/doc",
        display_url="ejemplo.com/doc",
    )
    assert con.display_url == "ejemplo.com/doc"


def test_tweetlink_rechaza_url_vacia():
    with pytest.raises(ValidationError):
        TweetLink(url="", expanded_url="https://ejemplo.com")


def test_tweetlink_rechaza_expanded_url_vacia():
    with pytest.raises(ValidationError):
        TweetLink(url="https://t.co/abc", expanded_url="   ")


def test_tweetlink_expanded_igual_a_url_es_valido():
    # Un link que no se pudo expandir es válido a nivel modelo (lo filtra el mapper).
    link = TweetLink(url="https://t.co/abc", expanded_url="https://t.co/abc")
    assert link.expanded_url == link.url
