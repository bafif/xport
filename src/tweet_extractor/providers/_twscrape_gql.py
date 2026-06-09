from __future__ import annotations

from typing import Any

from twscrape import AccountsPool
from twscrape.api import GQL_FEATURES, GQL_URL, OP_SearchTimeline
from twscrape.queue_client import QueueClient
from twscrape.utils import encode_params

from tweet_extractor.providers.base import ProviderError


def _build_params(query_str: str, count: int, cursor: str | None) -> dict[str, Any]:
    """Arma los params del request SearchTimeline (variables + features + fieldToggles).
    Pura y testeable; el cursor se incluye solo si hay (la primera página va sin)."""
    variables: dict[str, Any] = {
        "rawQuery": query_str,
        "count": count,
        "product": "Latest",  # reverse-chrono (no relevancia) -> mejor cobertura por ventana
        "querySource": "typed_query",
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return {
        "variables": variables,
        "features": GQL_FEATURES,
        "fieldToggles": {"withArticleRichContentState": False},
    }


async def fetch_search_page(
    pool: AccountsPool | None, query_str: str, count: int, cursor: str | None
) -> dict[str, Any]:
    """UN request de página de SearchTimeline (el único seam de red del provider).
    `QueueClient` por-página: el estado de rate-limit vive en el `AccountsPool`
    (persistido), así que recrearlo respeta los locks; el backoff ante 429 lo maneja
    twscrape. `rep is None` -> `ProviderError` (no enmascarar como fin de resultados)."""
    if pool is None:
        raise ProviderError("AccountsPool no inicializado (usar build_pool)")
    params = _build_params(query_str, count, cursor)
    async with QueueClient(pool, "SearchTimeline", False) as client:
        rep = await client.get(f"{GQL_URL}/{OP_SearchTimeline}", params=encode_params(params))
    if rep is None:
        raise ProviderError(
            "twscrape no pudo completar el request de búsqueda "
            "(cuenta inválida/suspendida o rate-limit agotado)"
        )
    result: dict[str, Any] = rep.json()
    return result
