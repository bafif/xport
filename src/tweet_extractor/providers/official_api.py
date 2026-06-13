from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from tweet_extractor.config import Settings
from tweet_extractor.providers.base import Page, ProviderError, SearchQuery, TweetProvider

# Endpoint del timeline de usuario en la X API v2 (ver docs/plan §2). Soporta
# `start_time`/`end_time` (ISO 8601, `end_time` exclusivo), `max_results` ≤ 100 y
# paginación por `pagination_token`. El `:id` se resuelve antes vía
# `GET /2/users/by/username/:username`.
USER_TWEETS_ENDPOINT = "https://api.twitter.com/2/users/{user_id}/tweets"

# Tope de la v2: 100 resultados por página.
V2_MAX_RESULTS = 100

# La v2 por defecto solo devuelve id+text; hay que pedir explícitamente lo demás.
TWEET_FIELDS = "created_at,entities,referenced_tweets,conversation_id,author_id"
EXPANSIONS = "referenced_tweets.id,author_id"


def build_params(
    since: str, until: str, max_results: int, pagination_token: str | None
) -> dict[str, Any]:
    """Los query params del request a `/2/users/:id/tweets`. Pura y testeable:
    documenta la forma del request que hará el fetcher real cuando se implemente
    el backend (la primera página va sin `pagination_token`). `since`/`until` son
    ISO 8601 (`end_time` exclusivo, coherente con `until` exclusivo del dominio)."""
    params: dict[str, Any] = {
        "start_time": since,
        "end_time": until,
        "max_results": max_results,
        "tweet.fields": TWEET_FIELDS,
        "expansions": EXPANSIONS,
    }
    if pagination_token is not None:
        params["pagination_token"] = pagination_token
    return params


def extract_tweets_v2(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Los tweets de nivel-tope de la página: `data[]`. Los quotes/RT referenciados
    viven aparte en `includes.tweets[]` (no son entradas del timeline), así que NO
    se confunden con el nivel-tope. Defensivo: `data` ausente -> `[]`."""
    data = raw.get("data")
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def extract_next_token(raw: dict[str, Any]) -> str | None:
    """El `meta.next_token` para la próxima página, o `None` al final (corta la
    paginación heredada de `TweetProvider`)."""
    meta = raw.get("meta")
    token = meta.get("next_token") if isinstance(meta, dict) else None
    return token if isinstance(token, str) else None


def count_accessed_v2(raw: dict[str, Any]) -> int:
    """TODO objeto-tweet tocado por la respuesta: la página (`data`) MÁS los
    referenciados hidratados en `includes.tweets` (quotes/RT, aunque algunos no se
    persistan). Sobre-cuenta a propósito (los includes se deduplican a nivel API):
    la dirección SEGURA del cap (el ledger no deduplica)."""
    includes = raw.get("includes")
    included = includes.get("tweets") if isinstance(includes, dict) else None
    n_included = len(included) if isinstance(included, list) else 0
    return len(extract_tweets_v2(raw)) + n_included


async def fetch_user_tweets_page(
    settings: Settings, query: SearchQuery, cursor: str | None
) -> dict[str, Any]:
    """Seam de red del backend oficial. STUB de Fase 2: falla cerrado. La
    implementación real resolverá `username -> user_id`, llamará
    `GET {USER_TWEETS_ENDPOINT}` con `build_params(...)` y el Bearer token, y
    respetará `x-rate-limit-reset`. No se implementa con lógica sin verificar
    contra la API de pago real."""
    raise ProviderError(
        f"OfficialApiProvider es un stub de Fase 2: el fetch a {USER_TWEETS_ENDPOINT} "
        "no está implementado. Usá el backend twscrape (PROVIDER_BACKEND=twscrape)."
    )


PageFetcher = Callable[[Settings, SearchQuery, "str | None"], Awaitable[dict[str, Any]]]


class OfficialApiProvider(TweetProvider):
    """Backend de la X API v2 oficial (stub de Fase 2). Conforma `TweetProvider`
    para que el `GatedProvider` lo envuelva igual que al de scraping —el gate es
    agnóstico del backend— y para que la factory pueda intercambiarlos sin tocar
    el orquestador. El seam de red (`page_fetcher`) se inyecta: hoy el default
    falla cerrado; los helpers de envelope (`extract_tweets_v2`/`extract_next_token`/
    `count_accessed_v2`) ya parsean la forma v2 documentada."""

    def __init__(
        self,
        settings: Settings,
        *,
        page_fetcher: PageFetcher = fetch_user_tweets_page,
    ) -> None:
        # Cota de reserva del gate: la página entera (≤100) más sus referenciados
        # hidratados. Sobre-estima (fail-closed), igual criterio que el de twscrape.
        self.max_accessed_per_page = V2_MAX_RESULTS * settings.ACCESS_FACTOR_PER_TWEET
        self._settings = settings
        self._fetch = page_fetcher

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        raw = await self._fetch(self._settings, query, cursor)
        return Page(
            tweets=extract_tweets_v2(raw),
            accessed_count=count_accessed_v2(raw),
            next_cursor=extract_next_token(raw),
        )
