from __future__ import annotations

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider


class GatedProvider(TweetProvider):
    """Aplica el Compliance Gate a CUALQUIER TweetProvider. Reserva la cota
    superior ANTES de cada fetch, hace el request, y reconcilia al conteo real.
    Hereda `fetch_tweets`, que llama `self.fetch_page` (gateado): ningún acceso
    saltea el gate."""

    def __init__(self, inner: TweetProvider, gate: SlidingWindowGate) -> None:
        cap = getattr(inner, "max_accessed_per_page", None)
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise TypeError(
                f"{type(inner).__name__} debe definir max_accessed_per_page (int > 0): "
                "es la cota superior de accesos por página que reserva el Compliance Gate."
            )
        self._inner = inner
        self._gate = gate
        self.max_accessed_per_page = cap

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        # Reserva la cota superior ANTES del fetch. Si el inner lanza, la reserva
        # queda en la cota superior y NUNCA se reconcilia hacia abajo. Es intencional
        # y fail-closed: no sabemos cuántos objetos accedió el backend antes de fallar,
        # así que sobre-contar es lo seguro para un tope de cumplimiento. NO agregar un
        # try/finally que reconcilie a 0 — sería sub-contar y violaría el cap.
        rid = await self._gate.reserve(self.max_accessed_per_page)
        page = await self._inner.fetch_page(query, cursor)
        # Piso defensivo: el ledger nunca debe contar MENOS que los tweets que el
        # provider efectivamente entregó. Si un backend sub-reporta accessed_count,
        # sub-contar es la dirección INSEGURA del cap; nos quedamos con el máximo.
        accessed = max(page.accessed_count, len(page.tweets))
        await self._gate.reconcile(rid, accessed)
        return page
