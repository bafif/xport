from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tweet_extractor.config import Settings
from tweet_extractor.i18n import t
from tweet_extractor.providers.base import Page, ProviderError, SearchQuery, TweetProvider

# Backend de la X API v2 oficial (de pago, por lectura). Usa la búsqueda de archivo
# completo y no el timeline de usuario: el timeline solo llega a los ~3.200 posts más
# recientes, y un rango de fechas viejo quedaría afuera. Se armó con la documentación
# de X y tests con respuestas fabricadas: NO está verificado contra la API real (no hay
# una clave paga con qué probarlo). Si tu plan no incluye esta búsqueda, X responde 403
# y el error lo dice.
SEARCH_ALL_ENDPOINT = "https://api.x.com/2/tweets/search/all"

# Tope de `max_results` en la búsqueda de archivo completo.
V2_MAX_RESULTS = 500

# La v2 por defecto solo trae id y texto. Nada de `expansions`: el id del citado ya
# viene en `referenced_tweets`, y desde el 22/11/2026 X cobra lo que se expande.
TWEET_FIELDS = "created_at,entities,referenced_tweets,conversation_id,note_tweet"

# `end_time` tiene que quedar al menos 10 s antes del pedido; se deja más margen.
END_TIME_MARGIN = timedelta(seconds=30)
# La búsqueda de archivo completo admite un pedido por segundo.
MIN_INTERVAL_S = 1.0
MAX_RATE_LIMIT_WAITS = 5  # 429 seguidos antes de rendirse
MAX_RATE_LIMIT_WAIT_S = 15 * 60 + 5  # una ventana de rate limit de X, como mucho
MAX_RETRIES = 3  # errores 5xx o de red seguidos antes de rendirse


def build_query(username: str) -> str:
    """`from:<cuenta>` sin retweets: la regla los descarta igual, y pedirlos sería
    pagar (y gastar tope) por posts que se tiran."""
    return f"from:{username} -is:retweet"


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_params(
    query: SearchQuery, *, max_results: int, next_token: str | None, now: datetime
) -> dict[str, Any] | None:
    """Los query params de `/2/tweets/search/all`, o `None` si el tramo está entero en
    el futuro (no hay nada que pedir). `start_time` es inclusivo y `end_time`
    exclusivo, como `since`/`until` del dominio; `end_time` se recorta para quedar
    antes de `now - END_TIME_MARGIN`, que X exige."""
    end = min(query.until, now - END_TIME_MARGIN)
    if end <= query.since:
        return None
    params: dict[str, Any] = {
        "query": build_query(query.username),
        "start_time": _rfc3339(query.since),
        "end_time": _rfc3339(end),
        "max_results": max_results,
        "sort_order": "recency",
        "tweet.fields": TWEET_FIELDS,
    }
    if next_token is not None:
        params["next_token"] = next_token
    return params


def extract_tweets_v2(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Los posts de nivel-tope de la página: `data[]`. Los referenciados, si los
    hubiera, viven aparte en `includes` y no se confunden con el nivel-tope.
    Defensivo: `data` ausente (página vacía) -> `[]`."""
    data = raw.get("data")
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def extract_next_token(raw: dict[str, Any]) -> str | None:
    """El `meta.next_token` para la próxima página, o `None` al final (corta la
    paginación heredada de `TweetProvider`)."""
    meta = raw.get("meta")
    token = meta.get("next_token") if isinstance(meta, dict) else None
    return token if isinstance(token, str) else None


def count_accessed_v2(raw: dict[str, Any]) -> int:
    """TODO objeto-post que entrega la respuesta: la página (`data`) más los que
    vengan en `includes` (las docs los llaman `tweets` en unas páginas y `posts` en
    otras: se cuentan los dos). Sobre-cuenta a propósito: es la dirección SEGURA del
    tope (el ledger no deduplica)."""
    includes = raw.get("includes")
    n_included = 0
    if isinstance(includes, dict):
        for key in ("tweets", "posts"):
            items = includes.get(key)
            n_included += len(items) if isinstance(items, list) else 0
    return len(extract_tweets_v2(raw)) + n_included


def _describe(res: httpx.Response) -> str:
    """El error de X en palabras: el `title`/`detail` de su respuesta y una pista
    para los casos que dependen del plan o del token."""
    detail = ""
    try:
        body = res.json()
        if isinstance(body, dict):
            detail = " ".join(str(body[k]) for k in ("title", "detail") if body.get(k))
    except ValueError:
        detail = res.text[:200]
    hint = t(f"provider.api_{res.status_code}") if res.status_code in (401, 402, 403) else ""
    parts = [t("provider.api_status", status=res.status_code), hint, detail]
    return ": ".join(p for p in parts if p)


class SearchAllFetcher:
    """Seam de red del backend oficial: pide UNA página de `/2/tweets/search/all`.
    Respeta un pedido por segundo, espera ante 429 hasta `x-rate-limit-reset` y
    reintenta con espera creciente ante 5xx o errores de red. `transport`, `sleep` y
    `clock` se inyectan para los tests (sin red ni esperas reales)."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        token = settings.x_api_bearer_token
        if not token:
            raise ProviderError(t("provider.no_bearer"))
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._last_request = float("-inf")

    async def __call__(self, query: SearchQuery, cursor: str | None) -> dict[str, Any]:
        now = datetime.fromtimestamp(self._clock(), UTC)
        params = build_params(query, max_results=V2_MAX_RESULTS, next_token=cursor, now=now)
        if params is None:
            return {"meta": {"result_count": 0}}  # tramo en el futuro: no se pide nada
        rate_waits = retries = 0
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            while True:
                await self._pace()
                try:
                    res = await client.get(
                        SEARCH_ALL_ENDPOINT, params=params, headers=self._headers
                    )
                except httpx.TransportError as e:
                    retries += 1
                    if retries > MAX_RETRIES:
                        raise ProviderError(t("provider.api_unreachable", error=e)) from e
                    await self._sleep(2.0**retries)
                    continue
                if res.status_code == 200:
                    body = res.json()
                    if not isinstance(body, dict):
                        raise ProviderError(t("provider.api_not_json"))
                    return body
                if res.status_code == 429:
                    rate_waits += 1
                    if rate_waits > MAX_RATE_LIMIT_WAITS:
                        raise ProviderError(t("provider.rate_limited", reason=_describe(res)))
                    await self._sleep(self._rate_limit_wait(res, rate_waits))
                    continue
                if res.status_code >= 500:
                    retries += 1
                    if retries > MAX_RETRIES:
                        raise ProviderError(_describe(res))
                    await self._sleep(2.0**retries)
                    continue
                raise ProviderError(_describe(res))

    async def _pace(self) -> None:
        wait = self._last_request + MIN_INTERVAL_S - self._clock()
        if wait > 0:
            await self._sleep(wait)
        self._last_request = self._clock()

    def _rate_limit_wait(self, res: httpx.Response, attempt: int) -> float:
        """Hasta `x-rate-limit-reset` (epoch en segundos) si X lo manda; si no,
        espera creciente. Siempre entre 1 s y una ventana de rate limit."""
        reset = res.headers.get("x-rate-limit-reset")
        try:
            wait = float(reset) - self._clock() + 1.0 if reset is not None else 0.0
        except ValueError:
            wait = 0.0
        if wait <= 0:
            wait = 15.0 * 2 ** (attempt - 1)
        return max(1.0, min(wait, MAX_RATE_LIMIT_WAIT_S))


PageFetcher = Callable[[SearchQuery, "str | None"], Awaitable[dict[str, Any]]]


class OfficialApiProvider(TweetProvider):
    """Backend de la X API v2 oficial. Conforma `TweetProvider` para que el
    `GatedProvider` lo envuelva igual que al de scraping (el gate es agnóstico del
    backend) y para que la factory los intercambie sin tocar el orquestador. El seam de
    red (`page_fetcher`) se inyecta; por defecto es `SearchAllFetcher`, que falla con
    `ProviderError` si falta el token."""

    def __init__(self, settings: Settings, *, page_fetcher: PageFetcher | None = None) -> None:
        # Cota de reserva del gate: la página entera (≤500) por el factor de accesos.
        # Sin expansions la página trae solo `data`, así que sobre-estima de sobra.
        self.max_accessed_per_page = V2_MAX_RESULTS * settings.ACCESS_FACTOR_PER_TWEET
        self._fetch = page_fetcher if page_fetcher is not None else SearchAllFetcher(settings)

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        raw = await self._fetch(query, cursor)
        return Page(
            tweets=extract_tweets_v2(raw),
            accessed_count=count_accessed_v2(raw),
            next_cursor=extract_next_token(raw),
        )
