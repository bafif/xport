from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiosqlite


class ComplianceError(RuntimeError):
    """Un pedido no puede cumplirse sin violar el tope de ToS."""


class SlidingWindowGate:
    """Tope duro de ToS: nunca acceder a más de `hard_cap` objetos-tweet en
    cualquier ventana móvil de `window_s` segundos. Cuenta accesos (no lo
    guardado), no deduplica, persiste en SQLite y falla cerrado."""

    def __init__(
        self,
        db_path: str | Path,
        hard_cap: int = 900_000,
        window_s: int = 86_400,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._db_path = str(db_path)
        self._hard_cap = hard_cap
        self._window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    @property
    def hard_cap(self) -> int:
        return self._hard_cap

    async def setup(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS access_ledger (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts    INTEGER NOT NULL,
                    count INTEGER NOT NULL
                )"""
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ledger_ts ON access_ledger(ts)")
            await db.commit()

    async def _usage(self, db: aiosqlite.Connection, now: int) -> int:
        cur = await db.execute(
            "SELECT COALESCE(SUM(count), 0) FROM access_ledger WHERE ts > ?",
            (now - self._window_s,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def usage(self, now: int | None = None) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            moment = int(self._clock()) if now is None else now
            return await self._usage(db, moment)

    async def remaining(self, now: int | None = None) -> int:
        return self._hard_cap - await self.usage(now)

    async def reserve(self, n: int) -> int:
        """Reserva capacidad para hasta `n` accesos ANTES del fetch. Devuelve
        el id de reserva (para reconciliar)."""
        if n <= 0:
            raise ValueError("n debe ser > 0")
        if n > self._hard_cap:
            raise ComplianceError(f"pedido de {n} excede el hard_cap {self._hard_cap}")
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                now = int(self._clock())
                used = await self._usage(db, now)
                if used + n <= self._hard_cap:
                    cur = await db.execute(
                        "INSERT INTO access_ledger(ts, count) VALUES(?, ?)",
                        (now, n),
                    )
                    await db.commit()
                    return int(cur.lastrowid)  # type: ignore[arg-type]
        # TEMPORAL: Task 4 reemplaza esto por la espera de ventana deslizante.
        raise ComplianceError("sin presupuesto")

    async def reconcile(self, reservation_id: int, actual: int) -> None:
        """Ajusta la reserva al conteo real de objetos accedidos tras el fetch."""
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE access_ledger SET count = ? WHERE id = ?",
                    (actual, reservation_id),
                )
                await db.commit()
