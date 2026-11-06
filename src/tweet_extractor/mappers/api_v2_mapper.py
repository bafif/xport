from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from tweet_extractor.domain.models import Tweet
from tweet_extractor.mappers.base import MappedTweet, MapperError, clean_str, links_from_entities
from tweet_extractor.providers.base import RawTweet

# Mapper de la X API v2 oficial. La forma es PLANA, distinta del GraphQL anidado de la
# web, y sale de la documentación de X: no está verificada contra respuestas reales
# (no hay una clave paga con qué probarla). Por eso es estricto con lo que necesita y
# lanza `MapperError` ante una forma inesperada, en vez de exportar datos a medias.
#
#   id                    -> Tweet.id
#   text / note_tweet.text -> Tweet.content (los posts largos traen el texto entero
#                            en `note_tweet`; `text` viene recortado)
#   created_at            -> Tweet.created_at (RFC 3339, "2026-01-04T00:00:00.000Z")
#   referenced_tweets[]   -> {type: "retweeted" | "quoted" | "replied_to", id}
#       retweeted  -> descarte (None), regla dura del proyecto
#       quoted     -> quoted_tweet_id; url canónica /i/web/status/<id>
#       replied_to -> is_reply
#   conversation_id       -> MappedTweet.conversation_id
#   entities.urls[] y note_tweet.entities.urls[] -> links (mismo filtro que la web)


def map_tweet(raw: RawTweet, *, account: str) -> MappedTweet | None:
    """Mapea un objeto-post de `data[]` al dominio. Devuelve `None` para los retweets
    (descarte esperado) y lanza `MapperError` ante una forma inesperada. `account` es
    el handle consultado: lo estampa el caller, no se extrae del payload."""
    refs = _references(raw)
    if any(kind == "retweeted" for kind, _ in refs):
        return None
    tweet_id = clean_str(raw.get("id"))
    if tweet_id is None:
        raise MapperError(f"post sin `id` (claves presentes: {sorted(raw)})")
    quoted_id = next((ref_id for kind, ref_id in refs if kind == "quoted"), None)
    try:
        tweet = Tweet(
            id=tweet_id,
            account=account,
            created_at=_parse_created_at(raw.get("created_at"), tweet_id),
            content=_content(raw, tweet_id),
            links=links_from_entities(_url_entities(raw), own_id=tweet_id, quoted_id=quoted_id),
            quoted_tweet_id=quoted_id,
            quoted_tweet_url=f"https://x.com/i/web/status/{quoted_id}" if quoted_id else None,
        )
    except ValidationError as e:
        raise MapperError(f"post {tweet_id}: la forma violó el modelo: {e}") from e
    return MappedTweet(
        tweet=tweet,
        is_reply=any(kind == "replied_to" for kind, _ in refs),
        conversation_id=clean_str(raw.get("conversation_id")),
    )


def _references(raw: RawTweet) -> list[tuple[str, str]]:
    """Los `(type, id)` de `referenced_tweets`, ignorando entradas incompletas."""
    refs = raw.get("referenced_tweets")
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise MapperError("`referenced_tweets` no es una lista")
    out: list[tuple[str, str]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        kind, ref_id = clean_str(ref.get("type")), clean_str(ref.get("id"))
        if kind is not None and ref_id is not None:
            out.append((kind, ref_id))
    return out


def _parse_created_at(value: object, tweet_id: str) -> datetime:
    if not isinstance(value, str):
        raise MapperError(f"post {tweet_id}: sin `created_at` (¿falta tweet.fields?)")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise MapperError(f"post {tweet_id}: created_at no parseable: {value!r}") from e
    if parsed.utcoffset() is None:
        raise MapperError(f"post {tweet_id}: created_at sin zona horaria: {value!r}")
    return parsed


def _content(raw: RawTweet, tweet_id: str) -> str:
    note = raw.get("note_tweet")
    if isinstance(note, dict) and isinstance(note.get("text"), str) and note["text"]:
        return str(note["text"])
    text = raw.get("text")
    if not isinstance(text, str):
        raise MapperError(f"post {tweet_id}: sin `text`")
    return text


def _url_entities(raw: RawTweet) -> Iterator[Any]:
    note = raw.get("note_tweet")
    for holder in (raw, note if isinstance(note, dict) else None):
        entities = holder.get("entities") if isinstance(holder, dict) else None
        urls = entities.get("urls") if isinstance(entities, dict) else None
        if isinstance(urls, list):
            yield from urls
