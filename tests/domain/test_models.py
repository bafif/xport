from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tweet_extractor.domain.models import Tweet, TweetLink


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


def test_tweet_construccion_completa():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
        links=[TweetLink(url="https://t.co/x", expanded_url="https://e.com")],
        quoted_tweet_id="999",
        quoted_tweet_url="https://x.com/i/web/status/999",
    )
    assert t.id == "123"
    assert t.is_quote is True
    assert len(t.links) == 1


def test_tweet_construccion_minima_defaults():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
    )
    assert t.links == []
    assert t.quoted_tweet_id is None
    assert t.quoted_tweet_url is None
    assert t.is_quote is False


def test_tweet_rechaza_created_at_naive():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0),  # naive
            content="hola",
        )


def test_tweet_normaliza_created_at_a_utc():
    # -03:00 (Buenos Aires) → mismo instante, pero tzinfo == UTC en almacenamiento.
    ba = timezone(timedelta(hours=-3))
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 9, 0, tzinfo=ba),
        content="hola",
    )
    assert t.created_at.tzinfo == UTC
    assert t.created_at == datetime(2023, 6, 1, 12, 0, tzinfo=UTC)


def test_tweet_rechaza_id_vacio():
    with pytest.raises(ValidationError):
        Tweet(
            id="  ",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
        )


def test_tweet_rechaza_account_vacio():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
        )


def test_tweet_rechaza_quote_incoherente_solo_id():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
            quoted_tweet_id="999",
        )


def test_tweet_rechaza_quote_incoherente_solo_url():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
            quoted_tweet_url="https://x.com/i/web/status/999",
        )


def test_tweet_acepta_quote_coherente():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
        quoted_tweet_id="999",
        quoted_tweet_url="https://x.com/i/web/status/999",
    )
    assert t.is_quote is True


def test_tweet_es_inmutable():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
    )
    with pytest.raises(ValidationError):
        t.content = "otro"  # frozen: reasignar atributo falla
