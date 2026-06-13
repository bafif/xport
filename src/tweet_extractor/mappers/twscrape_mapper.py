from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from tweet_extractor.domain.models import Tweet, TweetLink
from tweet_extractor.providers.base import RawTweet

# Formato legacy de X: "Wed Jan 04 00:00:00 +0000 2023". Trae offset (%z) -> el
# datetime sale tz-aware, como exige el modelo. strptime usa los nombres de
# mes/día del locale del proceso: este formato requiere locale C/inglés (el
# default de Python salvo que alguien llame locale.setlocale).
_CREATED_AT_FORMAT = "%a %b %d %H:%M:%S %z %Y"

# Permalink de status en twitter.com/x.com (incluye /i/web/status/<id> y sufijos
# tipo /photo/1); captura el id numérico para detectar links autorreferenciales.
_STATUS_URL = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/(?:i/web|[^/]+)/status(?:es)?/(\d+)",
    re.IGNORECASE,
)


class MapperError(ValueError):
    """El backend entregó un objeto-tweet con shape inesperado (rotación del
    GraphQL de x.com o bug del provider). Se lanza fuerte —no se descarta en
    silencio— para que una rotación de shape se note como error y no como un
    CSV vacío. Distinto de los descartes ESPERADOS (RT, tombstone), que son
    `None`."""


@dataclass(frozen=True)
class MappedTweet:
    """Un `Tweet` de dominio + los metadatos de conversación que necesita la
    política de replies (que es por-colección, no por-tweet: ver
    `apply_reply_policy`). `conversation_id` es la raíz de la conversación
    (`legacy.conversation_id_str`); `is_reply` deriva de la presencia de
    `in_reply_to_status_id_str`."""

    tweet: Tweet
    is_reply: bool
    conversation_id: str | None


def map_tweet(raw: RawTweet, *, account: str) -> MappedTweet | None:
    """Mapea un `tweet_results.result` crudo (como lo entrega `TwscrapeProvider`)
    al dominio. Devuelve `None` para los descartes esperados: retweets (presencia
    de `legacy.retweeted_status_result`, regla dura del proyecto) y no-tweets
    (tombstones/unavailable). Lanza `MapperError` ante shape malformado.

    Quotes: se incluyen como vínculo (`quoted_tweet_id`/`url`); el quote embebido
    NO se emite como fila (ya contó como acceso en el gate, vía `count_accessed`).
    """
    node = _unwrap(raw)
    if node is None:
        return None
    legacy = node.get("legacy")
    if not isinstance(legacy, dict):
        raise MapperError(f"tweet sin `legacy` (claves presentes: {sorted(node)})")
    if "retweeted_status_result" in legacy:
        return None
    tweet_id = _clean_str(node.get("rest_id")) or _clean_str(legacy.get("id_str"))
    if tweet_id is None:
        raise MapperError("tweet sin `rest_id` ni `legacy.id_str`")
    quoted_id, quoted_url = _extract_quote(node, legacy)
    try:
        tweet = Tweet(
            id=tweet_id,
            account=account,
            created_at=_parse_created_at(legacy.get("created_at"), tweet_id),
            content=_extract_content(node, legacy),
            links=_extract_links(node, legacy, own_id=tweet_id, quoted_id=quoted_id),
            quoted_tweet_id=quoted_id,
            quoted_tweet_url=quoted_url,
        )
    except ValidationError as e:
        # El modelo es la última línea de defensa de *forma*; si rechaza lo que
        # armamos, el shape crudo violó un supuesto del mapper -> mismo canal de error.
        raise MapperError(f"tweet {tweet_id}: el shape crudo violó el modelo: {e}") from e
    return MappedTweet(
        tweet=tweet,
        is_reply=_clean_str(legacy.get("in_reply_to_status_id_str")) is not None,
        conversation_id=_clean_str(legacy.get("conversation_id_str")),
    )


def apply_reply_policy(mapped: Iterable[MappedTweet]) -> list[Tweet]:
    """Política de replies POR RAÍZ DE CONVERSACIÓN (decisión en ESTADO.md):
    self-threads sí, replies a conversaciones ajenas no. Un reply se conserva
    sólo si su `conversation_id` (la raíz) es un tweet de la propia cuenta —
    eso excluye también al self-reply que cuelga de una conversación ajena,
    aunque responda a un tweet propio. Reply sin `conversation_id` -> se
    descarta (no se puede probar que es self-thread; cerrado por defecto).

    Aplicar sobre la colección COMPLETA de la cuenta (todas las sub-ventanas
    del job), no por página: la raíz suele aparecer en otra página/tramo.
    Limitación conocida: si la raíz quedó fuera del rango de fechas pedido, sus
    replies en-rango se descartan. Preserva el orden de entrada.
    """
    items = list(mapped)
    own_ids = {m.tweet.id for m in items}
    return [m.tweet for m in items if not m.is_reply or m.conversation_id in own_ids]


def _unwrap(result: dict[str, Any]) -> dict[str, Any] | None:
    """Desenvuelve `TweetWithVisibilityResults` (el tweet real está bajo `.tweet`,
    incluso anidado) y devuelve el nodo-tweet, o `None` si no es un tweet
    (tombstone/unavailable: descarte esperado). `__typename` ausente se asume
    `Tweet` (payloads viejos lo omitían)."""
    node = result
    while node.get("__typename") == "TweetWithVisibilityResults":
        inner = node.get("tweet")
        if not isinstance(inner, dict):
            raise MapperError("TweetWithVisibilityResults sin `.tweet` interno")
        node = inner
    if node.get("__typename", "Tweet") != "Tweet":
        return None
    return node


def _unwrap_lenient(result: dict[str, Any]) -> dict[str, Any] | None:
    """Variante para el quote EMBEBIDO: un quote malformado no debe tumbar al
    citante (el dato secundario se degrada al fallback `quoted_status_id_str`)."""
    try:
        return _unwrap(result)
    except MapperError:
        return None


def _parse_created_at(value: object, tweet_id: str) -> datetime:
    if not isinstance(value, str):
        raise MapperError(f"tweet {tweet_id}: sin `legacy.created_at`")
    try:
        return datetime.strptime(value, _CREATED_AT_FORMAT)
    except ValueError as e:
        raise MapperError(f"tweet {tweet_id}: created_at no parseable: {value!r}") from e


def _extract_content(node: dict[str, Any], legacy: dict[str, Any]) -> str:
    """`legacy.full_text` trunca los tweets largos (note tweets) con "…": si hay
    `note_tweet`, su texto completo es el contenido real."""
    note_text = _dig(node, "note_tweet", "note_tweet_results", "result", "text")
    if isinstance(note_text, str) and note_text:
        return note_text
    full_text = legacy.get("full_text")
    return full_text if isinstance(full_text, str) else ""


def _extract_quote(node: dict[str, Any], legacy: dict[str, Any]) -> tuple[str | None, str | None]:
    """El vínculo al tweet citado. El quote vive en `quoted_status_result.result`
    (y, defensivamente, en `legacy.quoted_status_result`); si está tombstoneado o
    ausente, cae a `legacy.quoted_status_id_str`. URL: el permalink que entrega X
    o, si falta, la forma canónica `/i/web/status/<id>` (no requiere handle)."""
    container = node.get("quoted_status_result") or legacy.get("quoted_status_result")
    quoted_id: str | None = None
    if isinstance(container, dict):
        result = container.get("result")
        if isinstance(result, dict):
            quoted_node = _unwrap_lenient(result)
            if quoted_node is not None:
                quoted_id = _clean_str(quoted_node.get("rest_id"))
    if quoted_id is None:
        quoted_id = _clean_str(legacy.get("quoted_status_id_str"))
    if quoted_id is None:
        return None, None
    permalink = _dig(legacy, "quoted_status_permalink", "expanded")
    url = _clean_str(permalink) or f"https://x.com/i/web/status/{quoted_id}"
    return quoted_id, url


def _extract_links(
    node: dict[str, Any], legacy: dict[str, Any], *, own_id: str, quoted_id: str | None
) -> list[TweetLink]:
    """Los links embebidos exportables: `legacy.entities.urls[]` más los del
    `entity_set` del note tweet (los tweets largos ponen sus links ahí). Se
    descartan entradas sin `expanded_url` y las autorreferenciales (permalink
    del propio tweet o de su quote: ruido, no contenido); un link a un TERCER
    tweet sí se conserva. Dedupe por (url, expanded_url) preservando orden."""
    links: list[TweetLink] = []
    seen: set[tuple[str, str]] = set()
    for entity in _iter_url_entities(node, legacy):
        url = _clean_str(entity.get("url"))
        expanded = _clean_str(entity.get("expanded_url"))
        if url is None or expanded is None:
            continue
        match = _STATUS_URL.match(expanded)
        if match is not None and match.group(1) in {own_id, quoted_id}:
            continue
        key = (url, expanded)
        if key in seen:
            continue
        seen.add(key)
        display = entity.get("display_url")
        links.append(
            TweetLink(
                url=url,
                expanded_url=expanded,
                display_url=display if isinstance(display, str) else None,
            )
        )
    return links


def _iter_url_entities(node: dict[str, Any], legacy: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for urls in (
        _dig(legacy, "entities", "urls"),
        _dig(node, "note_tweet", "note_tweet_results", "result", "entity_set", "urls"),
    ):
        if isinstance(urls, list):
            for entity in urls:
                if isinstance(entity, dict):
                    yield entity


def _dig(obj: Any, *keys: str) -> Any:
    """Navegación defensiva: `obj[k1][k2]...` tolerando claves ausentes o tipos
    inesperados en el camino (-> `None`), sin lanzar."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _clean_str(value: object) -> str | None:
    """str no-vacío trimeado, o `None` (ausente, vacío, whitespace o no-str)."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None
