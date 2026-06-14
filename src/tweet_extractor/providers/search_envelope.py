from __future__ import annotations

from collections.abc import Iterator
from typing import Any

# Navegación del ENVELOPE de SearchTimeline (puro, sin twscrape): localiza los
# `tweet_results.result` de nivel-tope, el cursor `Bottom` y cuenta los accesos.
# NO interpreta el tweet (typename/quote/RT/links) — eso es del mapper. Lo usan
# tanto el `TwscrapeProvider` (scraping activo) como la ingesta in-page (patrón C),
# así que vive en un módulo neutral que NO importa twscrape.

_TWEET_TYPENAMES = frozenset({"Tweet", "TweetWithVisibilityResults"})


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    """Recorre una estructura JSON anidada (depth-first, pre-orden) y hace yield de
    cada dict. ITERATIVO (pila explícita, no recursión): el envelope de SearchTimeline
    es acotado, pero por `/ingest` llega JSON no confiable y una recursión se pasaría
    del límite (RecursionError → 500). Se apilan los hijos en reversa para preservar
    el orden de documento."""
    stack: list[Any] = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


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
