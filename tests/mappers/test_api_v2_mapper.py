from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tweet_extractor.mappers import api_v2_mapper
from tweet_extractor.mappers.base import Mapper, MapperError

# Posts fabricados con la forma que documenta X para la API v2: no hay una clave paga
# con qué compararlos contra respuestas reales.


def post(tweet_id: str = "100", **over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": tweet_id,
        "text": "hola https://t.co/abc",
        "created_at": "2026-01-04T12:30:00.000Z",
        "conversation_id": tweet_id,
        "entities": {
            "urls": [
                {
                    "start": 5,
                    "end": 28,
                    "url": "https://t.co/abc",
                    "expanded_url": "https://ejemplo.org/doc.pdf",
                    "display_url": "ejemplo.org/doc.pdf",
                }
            ]
        },
    }
    raw.update(over)
    return raw


def test_conforma_el_protocol_mapper():
    mapper: Mapper = api_v2_mapper.map_tweet  # falla en mypy si la firma no encaja
    assert callable(mapper)


def test_mapea_un_post_comun():
    m = api_v2_mapper.map_tweet(post(), account="someuser")
    assert m is not None
    assert m.tweet.id == "100"
    assert m.tweet.account == "someuser"
    assert m.tweet.created_at == datetime(2026, 1, 4, 12, 30, tzinfo=UTC)
    assert m.tweet.content == "hola https://t.co/abc"
    assert [link.expanded_url for link in m.tweet.links] == ["https://ejemplo.org/doc.pdf"]
    assert m.tweet.quoted_tweet_id is None
    assert m.is_reply is False
    assert m.conversation_id == "100"


def test_retweet_se_descarta():
    raw = post(referenced_tweets=[{"type": "retweeted", "id": "9"}])
    assert api_v2_mapper.map_tweet(raw, account="someuser") is None


def test_quote_da_id_y_url_y_saca_el_permalink_de_los_links():
    raw = post(
        referenced_tweets=[{"type": "quoted", "id": "555"}],
        entities={
            "urls": [
                {
                    "url": "https://t.co/q",
                    "expanded_url": "https://twitter.com/otro/status/555",
                    "display_url": "x.com/otro/status/555",
                }
            ]
        },
    )
    m = api_v2_mapper.map_tweet(raw, account="someuser")
    assert m is not None
    assert m.tweet.quoted_tweet_id == "555"
    assert m.tweet.quoted_tweet_url == "https://x.com/i/web/status/555"
    assert m.tweet.links == []  # el permalink del citado es ruido, no un link


def test_respuesta_marca_is_reply_y_conversacion():
    raw = post(
        "101", referenced_tweets=[{"type": "replied_to", "id": "100"}], conversation_id="100"
    )
    m = api_v2_mapper.map_tweet(raw, account="someuser")
    assert m is not None
    assert m.is_reply is True
    assert m.conversation_id == "100"


def test_post_largo_usa_note_tweet_y_sus_links():
    raw = post(
        text="recortado…",
        entities=None,
        note_tweet={
            "text": "el texto entero del post largo",
            "entities": {
                "urls": [{"url": "https://t.co/n", "expanded_url": "https://ejemplo.org/largo"}]
            },
        },
    )
    m = api_v2_mapper.map_tweet(raw, account="someuser")
    assert m is not None
    assert m.tweet.content == "el texto entero del post largo"
    assert [link.expanded_url for link in m.tweet.links] == ["https://ejemplo.org/largo"]


@pytest.mark.parametrize(
    "roto",
    [
        {"id": None},
        {"created_at": None},
        {"created_at": "ayer"},
        {"created_at": "2026-01-04T12:30:00"},  # sin zona horaria
        {"text": None},
        {"referenced_tweets": "quoted"},
    ],
)
def test_forma_inesperada_lanza_mapper_error(roto: dict[str, Any]):
    with pytest.raises(MapperError):
        api_v2_mapper.map_tweet(post(**roto), account="someuser")
