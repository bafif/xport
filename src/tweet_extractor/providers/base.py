from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RawTweet = dict[str, Any]
"""JSON crudo de un objeto-tweet tal como lo entrega el backend.
El shape de dominio lo fija el mapper en su fase; el gate es agnóstico."""


@dataclass(frozen=True)
class SearchQuery:
    """Parámetros de una búsqueda `from:user since/until`."""

    username: str
    since: datetime
    until: datetime
    include_quotes: bool = True
    include_retweets: bool = False

    def __post_init__(self) -> None:
        if self.since.tzinfo is None or self.until.tzinfo is None:
            raise ValueError("SearchQuery.since y until deben ser timezone-aware (usar UTC)")
        if self.since >= self.until:
            raise ValueError("SearchQuery.since debe ser anterior a until")


@dataclass
class Page:
    """Una página de resultados de un provider.

    `accessed_count` cuenta TODO objeto-tweet tocado (citante + quote embebido +
    RT descartado); NO es `len(tweets)`.
    """

    tweets: list[RawTweet]
    accessed_count: int
    next_cursor: str | None = None


class TweetProvider(ABC):
    """Fuente de datos intercambiable. Las subclases implementan `fetch_page`;
    la paginación (`fetch_tweets`) es concreta y se hereda gratis."""

    max_accessed_per_page: int
    """Cota superior REAL de accesos por página (citante + quotes embebidos).
    DEBE sobre-estimar: si fuera menor que lo que el backend accede de verdad, el
    gate reservaría de menos y dejaría cruzar el cap. Sobre-estimar solo gasta
    presupuesto de más (fail-closed)."""

    @abstractmethod
    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        """Trae una página dado un cursor opaco. `None` = primera página."""
        ...

    async def fetch_tweets(self, query: SearchQuery) -> AsyncIterator[RawTweet]:
        """Pagina sobre `fetch_page` y hace yield de cada tweet.

        Continúa aunque una página traiga `tweets=[]` mientras `next_cursor`
        sea no-None (páginas parciales válidas de algunos backends). Corta si el
        backend repite el cursor (X devuelve el mismo "bottom cursor" al final de
        los resultados): sin este guard el bucle sería infinito y, vía el gate,
        drenaría todo el presupuesto diario en una sola query atascada.
        """
        cursor: str | None = None
        while True:
            page = await self.fetch_page(query, cursor)
            for tweet in page.tweets:
                yield tweet
            if page.next_cursor is None or page.next_cursor == cursor:
                break
            cursor = page.next_cursor
