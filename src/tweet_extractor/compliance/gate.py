from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import Any

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
        # Se setea en cada `reconcile`: despierta a quien espera presupuesto en
        # `reserve` sin que tenga que aguardar la ventana entera (ver `_wait_for_budget`).
        self._reconciled = asyncio.Event()
        # Conexión persistente perezosa (ver `_db`).
        self._conn: aiosqlite.Connection | None = None
        self._conn_lock = asyncio.Lock()

    @property
    def hard_cap(self) -> int:
        return self._hard_cap

    async def _db(self) -> aiosqlite.Connection:
        """Conexión persistente perezosa: UNA por gate en vez de abrir/cerrar en
        cada acceso (cada `aiosqlite.connect` arranca un hilo). aiosqlite serializa
        las operaciones en su hilo, así que compartirla entre `usage` (sin lock) y
        `reserve`/`reconcile` (con lock) es seguro: los SELECT de `usage` se encolan,
        nunca corren en paralelo con un write."""
        if self._conn is None:
            async with self._conn_lock:
                if self._conn is None:
                    self._conn = await aiosqlite.connect(self._db_path)
        return self._conn

    async def close(self) -> None:
        """Cierra la conexión persistente (shutdown limpio de la app)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> SlidingWindowGate:
        """`async with SlidingWindowGate(...) as gate:` deja el ledger listo
        (setup) y garantiza el cierre de la conexión al salir."""
        await self.setup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def setup(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await self._db()
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
        # Lectura sin lock intencional: solo `reserve` (bajo lock) hace crecer el uso,
        # así que un `usage` concurrente puede quedar levemente atrás pero nunca sub-cuenta el cap.
        db = await self._db()
        moment = int(self._clock()) if now is None else now
        return await self._usage(db, moment)

    async def remaining(self, now: int | None = None) -> int:
        return self._hard_cap - await self.usage(now)

    async def reserve(self, n: int) -> int:
        """Reserva capacidad para hasta `n` accesos ANTES del fetch. Bloquea
        (espera a que eventos viejos salgan de la ventana, o a que un `reconcile`
        libere presupuesto) si hace falta. Devuelve el id de reserva (para reconciliar)."""
        if n <= 0:
            raise ValueError("n debe ser > 0")
        if n > self._hard_cap:
            raise ComplianceError(f"pedido de {n} excede el hard_cap {self._hard_cap}")
        while True:
            async with self._lock:
                db = await self._db()
                now = int(self._clock())
                used = await self._usage(db, now)
                if used + n <= self._hard_cap:
                    # Poda lo que ya cayó fuera de la ventana: nunca se vuelve a contar
                    # (las queries filtran `ts > now - window_s`), y mantiene el ledger
                    # acotado al tamaño de una ventana en vez de crecer sin fin.
                    await db.execute(
                        "DELETE FROM access_ledger WHERE ts <= ?",
                        (now - self._window_s,),
                    )
                    cur = await db.execute(
                        "INSERT INTO access_ledger(ts, count) VALUES(?, ?)",
                        (now, n),
                    )
                    await db.commit()
                    rid = cur.lastrowid
                    assert rid is not None  # garantizado tras un INSERT exitoso
                    return rid
                wait_s = await self._wait_seconds(db, now)
                # Bajo lock: ningún `reconcile` posterior a este punto se pierde. Para
                # escribir tiene que tomar el lock, así que setea el evento DESPUÉS de
                # que lo limpiemos; no hay wakeup perdido.
                self._reconciled.clear()
            # Espera FUERA del lock: no bloquea a otras corrutinas ni al reconcile.
            await self._wait_for_budget(wait_s)

    async def _wait_for_budget(self, wait_s: float) -> None:
        """Espera hasta `wait_s` segundos O hasta que un `reconcile` libere
        presupuesto (lo que pase primero) y vuelve a evaluar. Reactivo: una página
        en vuelo que reconcilia hacia abajo despierta al que espera sin tener que
        aguardar la ventana entera (que puede ser de hasta 24 h)."""
        sleeper: asyncio.Future[Any] = asyncio.ensure_future(self._sleep(wait_s))
        freed: asyncio.Future[Any] = asyncio.ensure_future(self._reconciled.wait())
        waiters = [sleeper, freed]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in waiters:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def _wait_seconds(self, db: aiosqlite.Connection, now: int) -> float:
        """Segundos hasta que el evento más viejo de la ventana caiga afuera."""
        cur = await db.execute(
            "SELECT MIN(ts) FROM access_ledger WHERE ts > ?",
            (now - self._window_s,),
        )
        row = await cur.fetchone()
        oldest = row[0] if row and row[0] is not None else None
        if oldest is None:
            # Defensivo/inalcanzable: solo se llega acá con used + n > hard_cap, lo que
            # implica que hay eventos en la ventana (n > hard_cap ya falló antes).
            return 1.0
        return float(max(1, (oldest + self._window_s) - now))

    async def reconcile(self, reservation_id: int, actual: int) -> None:
        """Ajusta la reserva al conteo real de objetos accedidos tras el fetch."""
        async with self._lock:
            db = await self._db()
            await db.execute(
                "UPDATE access_ledger SET count = ? WHERE id = ?",
                (actual, reservation_id),
            )
            await db.commit()
        # Puede haber liberado presupuesto: despierta a quien espera en `reserve`.
        self._reconciled.set()

    async def record(self, n: int) -> int:
        """Registra `n` accesos YA ocurridos (captura in-page del patrón C): inserta
        en el ledger bajo lock, SIN chequeo de presupuesto ni espera. A diferencia de
        `reserve` (reserva-antes-y-bloquea), acá el acceso ya pasó en el browser del
        usuario: no hay nada que reservar, solo se asienta para que el ledger global
        siga siendo veraz across backends. Devuelve el uso resultante; el caller
        decide qué hacer si supera el cap (rechazar ingestas siguientes). El ledger
        NO deduplica (regla #1): sobre-contar es seguro, sub-contar no."""
        if n <= 0:
            raise ValueError("n debe ser > 0")
        async with self._lock:
            db = await self._db()
            now = int(self._clock())
            # Misma poda que `reserve`: acota el ledger a una ventana, nunca re-cuenta.
            await db.execute("DELETE FROM access_ledger WHERE ts <= ?", (now - self._window_s,))
            await db.execute("INSERT INTO access_ledger(ts, count) VALUES(?, ?)", (now, n))
            await db.commit()
            return await self._usage(db, now)
