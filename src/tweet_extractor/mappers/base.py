from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Protocol

from tweet_extractor.domain.models import Tweet
from tweet_extractor.providers.base import RawTweet

# Capa de mappers compartida (agnóstica de backend). Cada backend (twscrape,
# API v2 oficial) tiene su `map_tweet` concreto, pero TODOS producen el mismo
# `MappedTweet`, reportan errores de shape con `MapperError` y alimentan la
# misma política de replies. La factory aparea provider + mapper.


class MapperError(ValueError):
    """El backend entregó un objeto-tweet con shape inesperado (rotación del
    GraphQL/JSON del backend o bug del provider). Se lanza fuerte —no se descarta
    en silencio— para que una rotación de shape se note como error y no como un
    CSV vacío. Distinto de los descartes ESPERADOS (RT, tombstone), que son
    `None`."""


@dataclass(frozen=True)
class MappedTweet:
    """Un `Tweet` de dominio + los metadatos de conversación que necesita la
    política de replies (que es por-colección, no por-tweet: ver
    `apply_reply_policy`). `conversation_id` es la raíz de la conversación;
    `is_reply` indica si el tweet responde a otro."""

    tweet: Tweet
    is_reply: bool
    conversation_id: str | None


class Mapper(Protocol):
    """El contrato de un mapper de backend: convierte un objeto-tweet crudo al
    dominio, o devuelve `None` para un descarte ESPERADO (retweet/tombstone).
    Lanza `MapperError` ante shape malformado. `account` es el handle consultado
    (lo estampa el caller, no se extrae del payload). La factory selecciona la
    implementación concreta según el backend; el orquestador la invoca sin saber
    cuál es."""

    def __call__(self, raw: RawTweet, *, account: str) -> MappedTweet | None: ...


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
    replies en-rango se descartan. Preserva el orden de entrada."""
    items = list(mapped)
    own_ids = {m.tweet.id for m in items}
    return [
        m.tweet
        for m in items
        if passes_reply_policy(
            is_reply=m.is_reply, conversation_id=m.conversation_id, own_ids=own_ids
        )
    ]


def passes_reply_policy(
    *, is_reply: bool, conversation_id: str | None, own_ids: AbstractSet[str]
) -> bool:
    """El predicado por-tweet de la política (única fuente de la regla): no-reply
    pasa siempre; un reply pasa sólo si su raíz está entre los ids propios.
    Separado de `apply_reply_policy` para poder filtrar en STREAMING (p.ej. el
    export desde storage: `own_ids` ya persistidos + filas de a una) sin
    materializar la colección entera."""
    return not is_reply or conversation_id in own_ids
