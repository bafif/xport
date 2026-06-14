from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from twscrape import AccountsPool

from tweet_extractor.config import Settings
from tweet_extractor.providers._twscrape_gql import fetch_search_page
from tweet_extractor.providers.base import Page, ProviderError, SearchQuery, TweetProvider

# Helpers de envelope (movidos a `search_envelope`, módulo neutral sin twscrape, para
# que la ingesta in-page los reuse). Se re-exportan acá: `fetch_page` los usa y los
# imports históricos (`from ...twscrape_provider import extract_tweet_results`) siguen.
from tweet_extractor.providers.search_envelope import (
    count_accessed,
    extract_bottom_cursor,
    extract_tweet_results,
)


def build_query(query: SearchQuery) -> str:
    """La query de búsqueda `from:user since: until:` con operadores POR FECHA
    (granularidad de día), alineada a UTC. Se eligen `since:`/`until:` sobre
    `since_time:`/`until_time:` (epoch): mismo costo por request, los segundos no se
    necesitan, y son los operadores más battle-tested (menor riesgo de no-op del
    filtro). `until:` es exclusivo y `since:` inclusivo -> ventanas adyacentes no
    solapan. `query.since/until` son UTC tz-aware (garantía de `SearchQuery`)."""
    return f"from:{query.username} since:{query.since:%Y-%m-%d} until:{query.until:%Y-%m-%d}"


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


async def build_pool(settings: Settings) -> AccountsPool:
    """Arma el `AccountsPool` de twscrape con UNA cuenta desde las cookies del `.env`
    (estructura pool-friendly para crecer a N cuentas después). Con `ct0` presente la
    cuenta queda activa sin login. Idempotente (twscrape persiste en `accounts.db`)."""
    if not settings.x_auth_token or not settings.x_ct0:
        raise ProviderError(
            "Faltan cookies X_AUTH_TOKEN/X_CT0 en el entorno (.env). "
            "Cargá una cuenta de X descartable."
        )
    settings.accounts_db_path.parent.mkdir(parents=True, exist_ok=True)
    pool = AccountsPool(db_file=str(settings.accounts_db_path))
    if await pool.get_account("xport-session") is None:
        await pool.add_account(
            username="xport-session",
            password="",
            email="",
            email_password="",
            cookies=f"auth_token={settings.x_auth_token}; ct0={settings.x_ct0}",
        )
    return pool
