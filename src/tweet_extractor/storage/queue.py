"""La cola de capturas (punto 7 de docs/PLAN.md).

Una captura es una cuenta y un rango `[since, until)` que crawlea la extensión. Se encolan
desde la página local, el popup o la CLI, y la extensión las toma de a una: X frena la
búsqueda por cuenta de X, así que en paralelo no rinde. Vive en SQLite en la carpeta de
datos para sobrevivir a un reinicio, y la escriben dos procesos (el servicio y la CLI): lo
que lee y decide va en una transacción `BEGIN IMMEDIATE`.

Estados: `queued` (en cola), `running` (en curso), `paused` (en pausa: su pestaña quedó
oculta), `done` (terminada) y `stopped` (cortada, con su motivo en `note`). Una terminada o
cortada no cambia más: un aviso tardío de la extensión no la revive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account          TEXT NOT NULL,
    since            TEXT NOT NULL,  -- YYYY-MM-DD, inclusiva
    until            TEXT NOT NULL,  -- YYYY-MM-DD, exclusiva
    encoding         TEXT NOT NULL,
    delimiter        TEXT NOT NULL,
    state            TEXT NOT NULL,
    note             TEXT,
    created_at       TEXT NOT NULL,  -- ISO 8601 UTC
    started_at       TEXT,
    finished_at      TEXT,
    last_page_at     TEXT,
    csv              TEXT,           -- nombre del CSV en captures_dir, al terminar
    exported         INTEGER,
    excluded_replies INTEGER
);
CREATE INDEX IF NOT EXISTS idx_captures_state ON captures(state, id);
"""

_COLUMNS = (
    "id, account, since, until, encoding, delimiter, state, note, created_at, started_at, "
    "finished_at, last_page_at, csv, exported, excluded_replies"
)


class CaptureState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    STOPPED = "stopped"


ACTIVE = (CaptureState.RUNNING, CaptureState.PAUSED)
FINISHED = (CaptureState.DONE, CaptureState.STOPPED)
_UNFINISHED = (CaptureState.QUEUED, *ACTIVE)


@dataclass(frozen=True)
class Capture:
    id: int
    account: str
    since: date
    until: date  # exclusiva
    encoding: str
    delimiter: str
    state: CaptureState
    note: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_page_at: datetime | None
    csv: str | None
    exported: int | None
    excluded_replies: int | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _capture(row: Sequence[object]) -> Capture:
    return Capture(
        id=int(str(row[0])),
        account=str(row[1]),
        since=date.fromisoformat(str(row[2])),
        until=date.fromisoformat(str(row[3])),
        encoding=str(row[4]),
        delimiter=str(row[5]),
        state=CaptureState(str(row[6])),
        note=None if row[7] is None else str(row[7]),
        created_at=datetime.fromisoformat(str(row[8])),
        started_at=_when(None if row[9] is None else str(row[9])),
        finished_at=_when(None if row[10] is None else str(row[10])),
        last_page_at=_when(None if row[11] is None else str(row[11])),
        csv=None if row[12] is None else str(row[12]),
        exported=None if row[13] is None else int(str(row[13])),
        excluded_replies=None if row[14] is None else int(str(row[14])),
    )


def _marks(n: int) -> str:
    return ", ".join("?" * n)


class CaptureQueue:
    """La cola persistente. Conexión perezosa, como `SqliteStore`."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
        return self._conn

    async def setup(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await self._db()
        await db.executescript(_SCHEMA)
        await db.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> CaptureQueue:
        await self.setup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[aiosqlite.Connection]:
        """Leer-decidir-escribir de una vez, también contra el otro proceso (la CLI)."""
        db = await self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        finally:
            if db.in_transaction:
                await db.rollback()

    async def _fetch(
        self, db: aiosqlite.Connection, where: str, params: Sequence[object]
    ) -> list[Capture]:
        cur = await db.execute(f"SELECT {_COLUMNS} FROM captures WHERE {where}", params)
        return [_capture(row) for row in await cur.fetchall()]

    async def add(
        self,
        accounts: Sequence[str],
        since: date,
        until: date,
        *,
        encoding: str,
        delimiter: str,
    ) -> list[Capture]:
        """Encola una captura por cuenta, en orden. Si la misma cuenta con el mismo rango ya
        está en cola o en curso, devuelve esa en vez de encolarla de nuevo."""
        ids: list[int] = []
        async with self._write() as db:
            for account in accounts:
                same = await self._fetch(
                    db,
                    f"account = ? AND since = ? AND until = ? AND state IN ({_marks(3)})"
                    " ORDER BY id LIMIT 1",
                    (account, since.isoformat(), until.isoformat(), *_UNFINISHED),
                )
                if same:
                    ids.append(same[0].id)
                    continue
                cur = await db.execute(
                    "INSERT INTO captures(account, since, until, encoding, delimiter, state,"
                    " created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        account,
                        since.isoformat(),
                        until.isoformat(),
                        encoding,
                        delimiter,
                        CaptureState.QUEUED,
                        _now(),
                    ),
                )
                assert cur.lastrowid is not None
                ids.append(cur.lastrowid)
            found = {c.id: c for c in await self._fetch(db, f"id IN ({_marks(len(ids))})", ids)}
        return [found[i] for i in ids]

    async def recent(self, limit: int = 100) -> list[Capture]:
        """Las últimas `limit` capturas, de la más nueva a la más vieja."""
        db = await self._db()
        return await self._fetch(db, "1 ORDER BY id DESC LIMIT ?", (limit,))

    async def get(self, capture_id: int) -> Capture | None:
        db = await self._db()
        found = await self._fetch(db, "id = ?", (capture_id,))
        return found[0] if found else None

    async def active(self) -> Capture | None:
        """La captura en curso o en pausa, si hay."""
        db = await self._db()
        found = await self._fetch(db, f"state IN ({_marks(2)}) ORDER BY id LIMIT 1", ACTIVE)
        return found[0] if found else None

    async def claim_next(self) -> Capture | None:
        """La que tiene que crawlear la extensión: la que está en curso o, si no hay, la más
        vieja de la cola, que pasa a en curso. `None` si no hay nada que hacer."""
        async with self._write() as db:
            found = await self._fetch(db, f"state IN ({_marks(2)}) ORDER BY id LIMIT 1", ACTIVE)
            if found:
                return found[0]
            queued = await self._fetch(db, "state = ? ORDER BY id LIMIT 1", (CaptureState.QUEUED,))
            if not queued:
                return None
            await db.execute(
                "UPDATE captures SET state = ?, started_at = ? WHERE id = ?",
                (CaptureState.RUNNING, _now(), queued[0].id),
            )
            return (await self._fetch(db, "id = ?", (queued[0].id,)))[0]

    async def set_state(
        self, capture_id: int, state: CaptureState, note: str | None = None
    ) -> tuple[Capture | None, bool]:
        """Pasa una captura a `state`. Devuelve la captura y si cambió: una terminada o
        cortada no cambia más, una en cola solo puede cortarse (cancelarse) y `queued` no
        es un destino. `(None, False)` si no existe."""
        async with self._write() as db:
            found = await self._fetch(db, "id = ?", (capture_id,))
            if not found:
                return None, False
            cur = found[0]
            allowed = (cur.state in ACTIVE and state != CaptureState.QUEUED) or (
                cur.state == CaptureState.QUEUED and state == CaptureState.STOPPED
            )
            if not allowed or cur.state == state:
                return cur, False
            finished = _now() if state in FINISHED else None
            await db.execute(
                "UPDATE captures SET state = ?, note = COALESCE(?, note), finished_at = ?"
                " WHERE id = ?",
                (state, note, finished, capture_id),
            )
            return (await self._fetch(db, "id = ?", (capture_id,)))[0], True

    async def record_export(
        self,
        capture_id: int,
        *,
        csv: str | None,
        exported: int,
        excluded_replies: int,
        note: str | None = None,
    ) -> Capture:
        """Anota el CSV que salió de una captura terminada (o, sin tweets, el aviso)."""
        async with self._write() as db:
            await db.execute(
                "UPDATE captures SET csv = ?, exported = ?, excluded_replies = ?,"
                " note = COALESCE(?, note) WHERE id = ?",
                (csv, exported, excluded_replies, note, capture_id),
            )
            return (await self._fetch(db, "id = ?", (capture_id,)))[0]

    async def touch(self, account: str) -> None:
        """Anota que entró una página de `account`: la hora de la última, para la página."""
        db = await self._db()
        await db.execute(
            f"UPDATE captures SET last_page_at = ? WHERE account = ? AND state IN ({_marks(2)})",
            (_now(), account, *ACTIVE),
        )
        await db.commit()
