from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from twscrape import AccountsPool

from tweet_extractor.config import Settings
from tweet_extractor.providers._twscrape_gql import fetch_search_page
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider

_TWEET_TYPENAMES = frozenset({"Tweet", "TweetWithVisibilityResults"})


def build_query(query: SearchQuery) -> str:
    """La query de búsqueda `from:user since: until:` con operadores POR FECHA
    (granularidad de día), alineada a UTC. Se eligen `since:`/`until:` sobre
    `since_time:`/`until_time:` (epoch): mismo costo por request, los segundos no se
    necesitan, y son los operadores más battle-tested (menor riesgo de no-op del
    filtro). `until:` es exclusivo y `since:` inclusivo -> ventanas adyacentes no
    solapan. `query.since/until` son UTC tz-aware (garantía de `SearchQuery`)."""
    return f"from:{query.username} since:{query.since:%Y-%m-%d} until:{query.until:%Y-%m-%d}"


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    """Recorre una estructura JSON anidada (depth-first) y hace yield de cada dict.
    La profundidad del envelope de SearchTimeline es acotada (decenas de niveles)."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def extract_tweet_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Los `tweet_results.result` crudos de nivel-tope (uno por entry `tweet-*`).
    NO interpreta el tweet (typename/quote/RT/links) — eso es del mapper. Los quotes/RT
    embebidos viven dentro de `result` (no son entries) -> no se confunden con tope.
    Navegación defensiva: tolera claves ausentes (página vacía -> `[]`)."""
    out: list[dict[str, Any]] = []
    for d in _walk(raw):
        entry_id = d.get("entryId")
        if not (isinstance(entry_id, str) and entry_id.startswith("tweet-")):
            continue
        content = d.get("content")
        item = content.get("itemContent") if isinstance(content, dict) else None
        tweet_results = item.get("tweet_results") if isinstance(item, dict) else None
        result = tweet_results.get("result") if isinstance(tweet_results, dict) else None
        if isinstance(result, dict):
            out.append(result)
    return out


def extract_bottom_cursor(raw: dict[str, Any]) -> str | None:
    """El cursor `Bottom` para la próxima página (mismo criterio que twscrape: el
    objeto con `cursorType == "Bottom"`). `None` al final -> la paginación heredada corta."""
    for d in _walk(raw):
        if d.get("cursorType") == "Bottom":
            value = d.get("value")
            return value if isinstance(value, str) else None
    return None


def count_accessed(raw: dict[str, Any]) -> int:
    """TODO objeto-tweet tocado por la respuesta (citante + quote embebido + RT
    descartado): cantidad de dicts con `__typename` de tweet en cualquier nivel.
    Sobre-cuenta a propósito (un TweetWithVisibilityResults cuenta 2: wrapper + `.tweet`)
    — la dirección SEGURA del cap (el ledger no deduplica; sobre-contar es seguro)."""
    return sum(1 for d in _walk(raw) if d.get("__typename") in _TWEET_TYPENAMES)


PageFetcher = Callable[[AccountsPool | None, str, int, "str | None"], Awaitable[dict[str, Any]]]


class TwscrapeProvider(TweetProvider):
    """Provider de scraping gratis vía twscrape (httpx, sin navegador). Entrega dicts
    crudos de GraphQL por página; el mapper los interpreta. Conforma `fetch_page`
    (un request por página, cursor externo) para que el `GatedProvider` lo gatee. El
    fetcher de red se inyecta (`page_fetcher`) -> tests offline con fixtures."""

    def __init__(
        self,
        settings: Settings,
        pool: AccountsPool | None,
        *,
        page_fetcher: PageFetcher = fetch_search_page,
    ) -> None:
        self.max_accessed_per_page = settings.max_accessed_per_page  # cota de reserva del gate
        self._count = settings.page_size
        self._pool = pool
        self._fetch = page_fetcher

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        raw = await self._fetch(self._pool, build_query(query), self._count, cursor)
        return Page(
            tweets=extract_tweet_results(raw),
            accessed_count=count_accessed(raw),
            next_cursor=extract_bottom_cursor(raw),
        )
