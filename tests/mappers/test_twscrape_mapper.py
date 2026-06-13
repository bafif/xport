from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tests.providers._fixtures import tweet_result, url_entity
from tweet_extractor.mappers.twscrape_mapper import (
    MappedTweet,
    MapperError,
    apply_reply_policy,
    map_tweet,
)


def mapear(raw: dict[str, Any]) -> MappedTweet:
    """Mapea esperando que NO sea un descarte (la mayoría de los tests)."""
    mapped = map_tweet(raw, account="someuser")
    assert mapped is not None
    return mapped


# --- mapeo básico ---


def test_mapea_tweet_basico():
    mapped = mapear(tweet_result("1"))
    t = mapped.tweet
    assert t.id == "1"
    assert t.account == "someuser"
    assert t.content == "texto 1"
    assert t.created_at == datetime(2023, 1, 4, tzinfo=UTC)
    assert t.links == []
    assert t.quoted_tweet_id is None
    assert t.quoted_tweet_url is None
    assert not t.is_quote
    assert not mapped.is_reply
    assert mapped.conversation_id == "1"  # raíz: conversation_id == propio id


def test_created_at_con_offset_se_normaliza_a_utc():
    raw = tweet_result("1", created_at="Wed Jan 04 21:30:00 -0300 2023")
    assert mapear(raw).tweet.created_at == datetime(2023, 1, 5, 0, 30, tzinfo=UTC)


def test_desenvuelve_tweet_with_visibility_results():
    raw = tweet_result("1", typename="TweetWithVisibilityResults")
    assert mapear(raw).tweet.id == "1"


def test_id_cae_a_legacy_id_str():
    raw = tweet_result("1")
    del raw["rest_id"]
    raw["legacy"]["id_str"] = "55"
    assert mapear(raw).tweet.id == "55"


# --- descartes esperados (None) ---


def test_descarta_retweet():
    raw = tweet_result("1", retweeted=tweet_result("rt1"))
    assert map_tweet(raw, account="someuser") is None


def test_descarta_retweet_dentro_de_tvr():
    raw = tweet_result("1", typename="TweetWithVisibilityResults", retweeted=tweet_result("rt1"))
    assert map_tweet(raw, account="someuser") is None


def test_descarta_tombstone():
    assert map_tweet({"__typename": "TweetTombstone"}, account="someuser") is None


# --- shape malformado (MapperError, fuerte: una rotación de shape debe notarse) ---


def test_tvr_sin_tweet_interno_es_error():
    with pytest.raises(MapperError):
        map_tweet({"__typename": "TweetWithVisibilityResults"}, account="someuser")


def test_sin_legacy_es_error():
    with pytest.raises(MapperError):
        map_tweet({"__typename": "Tweet", "rest_id": "1"}, account="someuser")


def test_sin_rest_id_ni_id_str_es_error():
    raw = tweet_result("1")
    del raw["rest_id"]
    with pytest.raises(MapperError):
        map_tweet(raw, account="someuser")


def test_created_at_invalido_es_error():
    with pytest.raises(MapperError):
        map_tweet(tweet_result("1", created_at="no es una fecha"), account="someuser")


def test_created_at_ausente_es_error():
    raw = tweet_result("1")
    del raw["legacy"]["created_at"]
    with pytest.raises(MapperError):
        map_tweet(raw, account="someuser")


# --- quotes ---


def test_quote_id_y_url_canonica():
    mapped = mapear(tweet_result("1", quoted=tweet_result("200")))
    assert mapped.tweet.quoted_tweet_id == "200"
    assert mapped.tweet.quoted_tweet_url == "https://x.com/i/web/status/200"
    assert mapped.tweet.is_quote


def test_quote_usa_permalink_si_presente():
    raw = tweet_result(
        "1",
        quoted=tweet_result("200"),
        quoted_permalink="https://twitter.com/alguien/status/200",
    )
    assert mapear(raw).tweet.quoted_tweet_url == "https://twitter.com/alguien/status/200"


def test_quote_en_legacy_defensivo():
    raw = tweet_result("1")
    raw["legacy"]["quoted_status_result"] = {"result": tweet_result("900")}
    assert mapear(raw).tweet.quoted_tweet_id == "900"


def test_quote_envuelto_en_tvr():
    quoted = tweet_result("200", typename="TweetWithVisibilityResults")
    assert mapear(tweet_result("1", quoted=quoted)).tweet.quoted_tweet_id == "200"


def test_quote_tombstone_cae_a_quoted_status_id_str():
    raw = tweet_result("1", quoted={"__typename": "TweetTombstone"})
    raw["legacy"]["quoted_status_id_str"] = "77"
    mapped = mapear(raw)
    assert mapped.tweet.quoted_tweet_id == "77"
    assert mapped.tweet.quoted_tweet_url == "https://x.com/i/web/status/77"


def test_quote_malformado_no_tumba_al_citante():
    # TVR sin `.tweet` DENTRO del quote: dato secundario -> se degrada, no MapperError.
    raw = tweet_result("1", quoted={"__typename": "TweetWithVisibilityResults"})
    assert mapear(raw).tweet.quoted_tweet_id is None


# --- links ---


def test_extrae_links():
    raw = tweet_result(
        "1",
        urls=[
            url_entity(
                "https://example.com/doc",
                url="https://t.co/a",
                display_url="example.com/doc",
            )
        ],
    )
    (link,) = mapear(raw).tweet.links
    assert link.url == "https://t.co/a"
    assert link.expanded_url == "https://example.com/doc"
    assert link.display_url == "example.com/doc"


def test_descarta_link_sin_expanded_o_vacio():
    raw = tweet_result(
        "1", urls=[{"url": "https://t.co/a"}, {"url": "https://t.co/b", "expanded_url": "  "}]
    )
    assert mapear(raw).tweet.links == []


def test_descarta_link_autorreferencial():
    raw = tweet_result(
        "100",
        urls=[
            url_entity("https://x.com/u/status/100", url="https://t.co/a"),
            url_entity("https://twitter.com/u/status/100/photo/1", url="https://t.co/b"),
            url_entity("https://mobile.twitter.com/i/web/status/100", url="https://t.co/c"),
        ],
    )
    assert mapear(raw).tweet.links == []


def test_descarta_link_al_quote_pero_no_a_terceros_tweets():
    raw = tweet_result(
        "100",
        quoted=tweet_result("200"),
        urls=[
            url_entity("https://x.com/otro/status/200", url="https://t.co/a"),
            url_entity("https://x.com/otro/status/300", url="https://t.co/b"),
        ],
    )
    (link,) = mapear(raw).tweet.links
    assert link.expanded_url == "https://x.com/otro/status/300"


def test_dedupe_links_preserva_orden():
    raw = tweet_result(
        "1",
        urls=[
            url_entity("https://a.com", url="https://t.co/a"),
            url_entity("https://b.com", url="https://t.co/b"),
            url_entity("https://a.com", url="https://t.co/a"),
        ],
    )
    assert [link.expanded_url for link in mapear(raw).tweet.links] == [
        "https://a.com",
        "https://b.com",
    ]


# --- note tweets (tweets largos) ---


def con_note_tweet(raw: dict[str, Any], text: str, urls: list[dict[str, Any]]) -> dict[str, Any]:
    raw["note_tweet"] = {
        "note_tweet_results": {"result": {"text": text, "entity_set": {"urls": urls}}}
    }
    return raw


def test_note_tweet_reemplaza_full_text_truncado():
    raw = con_note_tweet(tweet_result("1", full_text="truncado…"), "texto completo largo", [])
    assert mapear(raw).tweet.content == "texto completo largo"


def test_note_tweet_aporta_links_y_dedupea_contra_legacy():
    raw = tweet_result("1", urls=[url_entity("https://a.com", url="https://t.co/a")])
    con_note_tweet(
        raw,
        "largo",
        [
            url_entity("https://a.com", url="https://t.co/a"),  # duplicado de legacy
            url_entity("https://b.com", url="https://t.co/b"),
        ],
    )
    assert [link.expanded_url for link in mapear(raw).tweet.links] == [
        "https://a.com",
        "https://b.com",
    ]


# --- metadatos de reply + política por raíz de conversación ---


def test_reply_expone_metadatos():
    mapped = mapear(tweet_result("2", in_reply_to="1", conversation_id="1"))
    assert mapped.is_reply
    assert mapped.conversation_id == "1"


def test_policy_conserva_raices_y_self_threads():
    raiz = mapear(tweet_result("1"))
    hilo = mapear(tweet_result("2", in_reply_to="1", conversation_id="1"))
    assert [t.id for t in apply_reply_policy([raiz, hilo])] == ["1", "2"]


def test_policy_descarta_reply_a_conversacion_ajena():
    raiz = mapear(tweet_result("1"))
    ajeno = mapear(tweet_result("3", in_reply_to="900", conversation_id="900"))
    assert [t.id for t in apply_reply_policy([raiz, ajeno])] == ["1"]


def test_policy_descarta_self_reply_que_cuelga_de_conversacion_ajena():
    # El caso que motivó la definición por raíz (ESTADO.md): "4" responde a un tweet
    # PROPIO ("3"), pero la conversación la arrancó un tercero ("900") -> fuera.
    propio = mapear(tweet_result("3", in_reply_to="900", conversation_id="900"))
    self_reply = mapear(tweet_result("4", in_reply_to="3", conversation_id="900"))
    assert apply_reply_policy([propio, self_reply]) == []


def test_policy_descarta_reply_sin_conversation_id():
    # Sin raíz no se puede probar self-thread: cerrado por defecto.
    raw = tweet_result("5", in_reply_to="1")
    del raw["legacy"]["conversation_id_str"]
    raiz = mapear(tweet_result("1"))
    huerfano = mapear(raw)
    assert [t.id for t in apply_reply_policy([raiz, huerfano])] == ["1"]


def test_policy_descarta_reply_cuya_raiz_no_se_recolecto():
    # Limitación documentada: raíz fuera del rango pedido -> el reply en-rango cae.
    solo_reply = mapear(tweet_result("2", in_reply_to="1", conversation_id="1"))
    assert apply_reply_policy([solo_reply]) == []


def test_policy_preserva_orden_de_entrada():
    a = mapear(tweet_result("10"))
    b = mapear(tweet_result("11", in_reply_to="10", conversation_id="10"))
    c = mapear(tweet_result("12"))
    assert [t.id for t in apply_reply_policy([a, b, c])] == ["10", "11", "12"]
