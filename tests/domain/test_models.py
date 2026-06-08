from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo

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


def test_tweet_trimea_whitespace_de_id_y_account():
    # El modelo normaliza whitespace de BORDE (higiene de forma); quitar el @ es del mapper.
    t = Tweet(
        id="  123  ",
        account="  user  ",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
    )
    assert t.id == "123"
    assert t.account == "user"


def test_tweetlink_trimea_url_y_expanded_url():
    link = TweetLink(url="  https://t.co/abc  ", expanded_url="  https://ejemplo.com  ")
    assert link.url == "https://t.co/abc"
    assert link.expanded_url == "https://ejemplo.com"


def test_tweet_rechaza_id_int():
    # snowflake como string, no int (precisión): pydantic v2 no coerciona int->str.
    with pytest.raises(ValidationError):
        Tweet(
            id=123,
            account="user",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
        )


def test_tweet_rechaza_quoted_tweet_id_int():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="user",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
            quoted_tweet_id=999,
            quoted_tweet_url="https://x.com/i/web/status/999",
        )


def test_tweet_rechaza_quoted_tweet_id_vacio():
    # Quote degenerado: id "presente" pero vacío. is_quote no debe quedar True con basura.
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="user",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
            quoted_tweet_id="",
            quoted_tweet_url="https://x.com/i/web/status/999",
        )


def test_tweet_trimea_quoted_tweet_id_y_url():
    t = Tweet(
        id="123",
        account="user",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
        quoted_tweet_id="  999  ",
        quoted_tweet_url="  https://x.com/i/web/status/999  ",
    )
    assert t.quoted_tweet_id == "999"
    assert t.quoted_tweet_url == "https://x.com/i/web/status/999"
    assert t.is_quote is True


# --- Hardening post code-review ---


def test_tweet_rechaza_created_at_epoch_int():
    # fail-fast (D2): un epoch crudo (seg) del mapper no debe aceptarse silenciosamente.
    with pytest.raises(ValidationError):
        Tweet(id="1", account="u", created_at=1685620800, content="x")


def test_tweet_rechaza_created_at_epoch_ms():
    # peor aún: un mixup seg/ms daría una fecha plausible-pero-errada sin señal.
    with pytest.raises(ValidationError):
        Tweet(id="1", account="u", created_at=1685620800000, content="x")


def test_tweet_rechaza_created_at_string():
    # el contrato es un datetime ya parseado; el parseo de strings es del mapper.
    with pytest.raises(ValidationError):
        Tweet(id="1", account="u", created_at="2023-06-01T12:00:00Z", content="x")


def test_tweet_rechaza_created_at_con_utcoffset_none():
    # Un tzinfo "aware" cuyo utcoffset() devuelve None es semánticamente naive: rechazar.
    class _UtcoffsetNone(tzinfo):
        def utcoffset(self, dt):
            return None

        def tzname(self, dt):
            return "none"

        def dst(self, dt):
            return None

    with pytest.raises(ValidationError):
        Tweet(
            id="1",
            account="u",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=_UtcoffsetNone()),
            content="x",
        )


def test_tweet_normaliza_created_at_cruza_medianoche():
    # 23:00 -03:00 = 02:00 UTC del día SIGUIENTE: la conversión debe avanzar la fecha.
    ba = timezone(timedelta(hours=-3))
    t = Tweet(
        id="1",
        account="u",
        created_at=datetime(2023, 6, 1, 23, 0, tzinfo=ba),
        content="x",
    )
    assert t.created_at == datetime(2023, 6, 2, 2, 0, tzinfo=UTC)


def test_tweet_acepta_content_vacio():
    # Decisión deliberada: content libre puede ser vacío (tweets solo-media/link/quote).
    t = Tweet(id="1", account="u", created_at=datetime(2023, 6, 1, tzinfo=UTC), content="")
    assert t.content == ""


def test_tweet_rechaza_id_bool():
    # bool es subclase de int: el guardián de "snowflake como string" también lo cubre.
    with pytest.raises(ValidationError):
        Tweet(id=True, account="u", created_at=datetime(2023, 6, 1, tzinfo=UTC), content="x")


def test_tweet_rechaza_campo_desconocido():
    # extra='forbid': un typo del mapper en un campo no se descarta en silencio.
    with pytest.raises(ValidationError):
        Tweet(
            id="1",
            account="u",
            created_at=datetime(2023, 6, 1, tzinfo=UTC),
            content="x",
            quoted_tweet_ur="typo",
        )


def test_tweet_valida_links_a_nivel_elemento():
    # Un TweetLink malformado dentro de links propaga ValidationError.
    with pytest.raises(ValidationError):
        Tweet(
            id="1",
            account="u",
            created_at=datetime(2023, 6, 1, tzinfo=UTC),
            content="x",
            links=[{"url": "", "expanded_url": "https://e.com"}],
        )


def test_tweetlink_display_url_whitespace_se_vuelve_none():
    link = TweetLink(url="https://t.co/a", expanded_url="https://e.com", display_url="   ")
    assert link.display_url is None


def test_tweetlink_rechaza_campo_desconocido():
    with pytest.raises(ValidationError):
        TweetLink(url="https://t.co/a", expanded_url="https://e.com", foo="bar")


def test_tweetlink_es_inmutable():
    link = TweetLink(url="https://t.co/a", expanded_url="https://e.com")
    with pytest.raises(ValidationError):
        link.url = "https://t.co/b"  # frozen: reasignar atributo falla
