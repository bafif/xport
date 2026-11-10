from __future__ import annotations

from datetime import date
from pathlib import Path

from tweet_extractor.storage.queue import CaptureQueue, CaptureState

SINCE, UNTIL = date(2024, 1, 1), date(2025, 1, 1)


async def _queue(tmp_path: Path) -> CaptureQueue:
    q = CaptureQueue(tmp_path / "queue.db")
    await q.setup()
    return q


async def test_encola_una_por_cuenta_sin_repetir(tmp_path: Path) -> None:
    q = await _queue(tmp_path)
    try:
        first = await q.add(["a", "b"], SINCE, UNTIL, encoding="utf-8", delimiter=",")
        again = await q.add(["b", "c"], SINCE, UNTIL, encoding="utf-8", delimiter=",")
        assert [c.account for c in first] == ["a", "b"]
        assert again[0].id == first[1].id  # la misma cuenta y rango en cola no se repite
        assert [c.account for c in await q.recent()] == ["c", "b", "a"]
        assert all(c.state == CaptureState.QUEUED for c in await q.recent())
    finally:
        await q.close()


async def test_va_de_a_una_y_en_orden(tmp_path: Path) -> None:
    q = await _queue(tmp_path)
    try:
        a, b = await q.add(["a", "b"], SINCE, UNTIL, encoding="utf-8", delimiter=",")
        took = await q.claim_next()
        assert took is not None and took.id == a.id and took.state == CaptureState.RUNNING
        assert took.started_at is not None
        # Mientras la primera sigue (también en pausa), la siguiente es la misma.
        await q.set_state(a.id, CaptureState.PAUSED)
        again = await q.claim_next()
        assert again is not None and again.id == a.id
        await q.set_state(a.id, CaptureState.DONE)
        second = await q.claim_next()
        assert second is not None and second.id == b.id
        await q.set_state(b.id, CaptureState.STOPPED, "x.com cambió el formato")
        assert await q.claim_next() is None
    finally:
        await q.close()


async def test_una_terminada_no_cambia_mas(tmp_path: Path) -> None:
    q = await _queue(tmp_path)
    try:
        (a,) = await q.add(["a"], SINCE, UNTIL, encoding="utf-8", delimiter=",")
        # En cola solo se puede cancelar: un «terminada» sin haber empezado no vale.
        assert (await q.set_state(a.id, CaptureState.DONE))[1] is False
        await q.claim_next()
        cap, changed = await q.set_state(a.id, CaptureState.DONE)
        assert changed and cap is not None and cap.finished_at is not None
        # Un aviso tardío de la extensión no la revive.
        cap, changed = await q.set_state(a.id, CaptureState.PAUSED)
        assert not changed and cap is not None and cap.state == CaptureState.DONE
        assert await q.set_state(999, CaptureState.DONE) == (None, False)
    finally:
        await q.close()


async def test_cancelar_una_en_cola(tmp_path: Path) -> None:
    q = await _queue(tmp_path)
    try:
        (a,) = await q.add(["a"], SINCE, UNTIL, encoding="utf-8", delimiter=",")
        cap, changed = await q.set_state(a.id, CaptureState.STOPPED, "La cancelaste.")
        assert changed and cap is not None and cap.note == "La cancelaste."
        assert await q.claim_next() is None
    finally:
        await q.close()


async def test_sobrevive_a_un_reinicio(tmp_path: Path) -> None:
    q = await _queue(tmp_path)
    (a,) = await q.add(["a"], SINCE, UNTIL, encoding="utf-8-sig", delimiter=";")
    await q.claim_next()
    await q.touch("a")
    await q.close()

    async with CaptureQueue(tmp_path / "queue.db") as q2:
        cap = await q2.active()
        assert cap is not None and cap.id == a.id
        assert (cap.encoding, cap.delimiter) == ("utf-8-sig", ";")
        assert cap.last_page_at is not None


async def test_sabe_si_queda_algo_sin_terminar(tmp_path: Path) -> None:
    """Lo que mira el navegador propio del servicio para levantarse o cerrarse."""
    q = await _queue(tmp_path)
    try:
        assert await q.has_unfinished() is False
        (cap,) = await q.add(["a"], SINCE, UNTIL, encoding="utf-8", delimiter=",")
        assert await q.has_unfinished() is True  # en cola
        await q.claim_next()
        assert await q.has_unfinished() is True  # en curso
        await q.set_state(cap.id, CaptureState.DONE)
        assert await q.has_unfinished() is False
    finally:
        await q.close()
