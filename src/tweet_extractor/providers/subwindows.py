from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta


def subwindows(
    since: datetime, until: datetime, step_days: int = 7
) -> Iterator[tuple[datetime, datetime]]:
    """Trocea `[since, until)` en tramos de `step_days` días, sin solape ni hueco
    (el solape gastaría presupuesto del Compliance Gate). El último tramo se recorta
    a `until`. Función pura: el loop que la consume (un `SearchQuery` por tramo) es
    de la fase de orquestación, para no saltear el gate.

    Precondiciones (espejo de `SearchQuery`): `since`/`until` timezone-aware,
    `since < until`, `step_days >= 1`.
    """
    if since.utcoffset() is None or until.utcoffset() is None:
        raise ValueError("since y until deben ser timezone-aware (UTC)")
    if since >= until:
        raise ValueError("since debe ser anterior a until")
    if step_days < 1:
        raise ValueError("step_days debe ser >= 1")

    step = timedelta(days=step_days)
    start = since
    while start < until:
        end = min(start + step, until)
        yield (start, end)
        start = end
