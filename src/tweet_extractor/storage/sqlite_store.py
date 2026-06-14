from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import aiosqlite

from tweet_extractor.domain.models import Tweet, TweetLink
from tweet_extractor.mappers.twscrape_mapper import MappedTweet

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets (
    id               TEXT PRIMARY KEY,
    account          TEXT NOT NULL,
    created_at       TEXT NOT NULL,  -- ISO 8601 UTC (+00:00): ordena lexicográfica = cronológica
    content          TEXT NOT NULL,
    links            TEXT NOT NULL,  -- JSON: [{url, expanded_url, display_url}, ...]
    quoted_tweet_id  TEXT,
    quoted_tweet_url TEXT,
    is_reply         INTEGER NOT NULL,
    conversation_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tweets_account_created ON tweets(account, created_at);
CREATE TABLE IF NOT EXISTS window_checkpoints (
    account      TEXT NOT NULL,
    since        TEXT NOT NULL,  -- ISO 8601 UTC del tramo de subwindows()
    until        TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (account, since, until)
);
"""


class SqliteStore:
    """SQLite intermedio de DATOS (separado del ledger de auditoría del gate, que
    vive en otro archivo y sobrevive si éste se borra/regenera).

    Persiste `MappedTweet` (el `Tweet` + los metadatos de conversación): la
    política de replies es por-colección y se aplica AL EXPORTAR, cuando ya está
    todo el job; persistir los metadatos permite reanudar a mitad de job sin
    perderla. Dedupe por PK con `INSERT OR IGNORE` (el mismo tweet puede caer en
    sub-ventanas solapadas). Checkpointing por sub-ventana: un tramo completado
    no se vuelve a fetchear en un re-run (cada re-fetch gasta presupuesto del gate).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        # Conexión persistente perezosa (mismo patrón que SlidingWindowGate;
        # aiosqlite serializa las operaciones en su hilo).
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

    async def __aenter__(self) -> SqliteStore:
        await self.setup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # --- tweets -----------------------------------------------------------------

    async def save(self, mapped: Iterable[MappedTweet]) -> int:
        """Inserta con `INSERT OR IGNORE` (dedupe por PK `id`; el primero gana —
        un tweet re-visto en otra sub-ventana es idéntico). Devuelve cuántas
        filas se insertaron de verdad (las ignoradas no cuentan)."""
        rows = [
            (
                m.tweet.id,
                m.tweet.account,
                m.tweet.created_at.isoformat(),  # ya UTC (lo garantiza el modelo)
                m.tweet.content,
                json.dumps([link.model_dump() for link in m.tweet.links]),
                m.tweet.quoted_tweet_id,
                m.tweet.quoted_tweet_url,
                int(m.is_reply),
                m.conversation_id,
            )
            for m in mapped
        ]
        if not rows:
            return 0
        db = await self._db()
        cur = await db.executemany(
            """INSERT OR IGNORE INTO tweets
               (id, account, created_at, content, links,
                quoted_tweet_id, quoted_tweet_url, is_reply, conversation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()
        return cur.rowcount

    async def iter_account(
        self,
        account: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> AsyncIterator[MappedTweet]:
        """Las filas de una cuenta en orden cronológico (el ISO UTC ordena
        lexicográficamente; empate intra-segundo desempatado por id, determinista).
        `since`/`until` (UTC tz-aware) filtran `created_at ∈ [since, until)` — lo usa
        el export por rango (la captura in-page acumula varias navegaciones). SIN
        política de replies: storage es tonto, el filtro lo compone el caller con
        `passes_reply_policy` + `account_ids` (streaming, ver csv_exporter)."""
        clauses = ["account = ?"]
        params: list[object] = [account]
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_utc_iso(since))
        if until is not None:
            clauses.append("created_at < ?")
            params.append(_utc_iso(until))
        db = await self._db()
        cur = await db.execute(
            f"""SELECT id, account, created_at, content, links,
                       quoted_tweet_id, quoted_tweet_url, is_reply, conversation_id
                FROM tweets WHERE {" AND ".join(clauses)} ORDER BY created_at, id""",
            params,
        )
        async for row in cur:
            yield MappedTweet(
                tweet=Tweet(
                    id=row[0],
                    account=row[1],
                    created_at=datetime.fromisoformat(row[2]),
                    content=row[3],
                    links=[TweetLink.model_validate(d) for d in json.loads(row[4])],
                    quoted_tweet_id=row[5],
                    quoted_tweet_url=row[6],
                ),
                is_reply=bool(row[7]),
                conversation_id=row[8],
            )

    async def account_ids(self, account: str) -> set[str]:
        """Los ids propios de la cuenta (el `own_ids` de la política de replies).
        Solo ids: cabe en memoria aun con cientos de miles de tweets."""
        db = await self._db()
        cur = await db.execute("SELECT id FROM tweets WHERE account = ?", (account,))
        return {row[0] for row in await cur.fetchall()}

    async def accounts(self) -> list[str]:
        db = await self._db()
        cur = await db.execute("SELECT DISTINCT account FROM tweets ORDER BY account")
        return [row[0] for row in await cur.fetchall()]

    # --- checkpoints de sub-ventanas ----------------------------------------------

    async def mark_window_done(self, account: str, since: datetime, until: datetime) -> None:
        """Marca un tramo de `subwindows()` como completado (todas sus páginas
        guardadas). Idempotente. Marcar SOLO al terminar el tramo: un tramo a
        medias debe re-correrse entero (el dedupe absorbe lo repetido)."""
        db = await self._db()
        await db.execute(
            """INSERT OR REPLACE INTO window_checkpoints (account, since, until, completed_at)
               VALUES (?, ?, ?, ?)""",
            (
                account,
                _utc_iso(since),
                _utc_iso(until),
                datetime.now(UTC).isoformat(),
            ),
        )
        await db.commit()

    async def is_window_done(self, account: str, since: datetime, until: datetime) -> bool:
        db = await self._db()
        cur = await db.execute(
            "SELECT 1 FROM window_checkpoints WHERE account = ? AND since = ? AND until = ?",
            (account, _utc_iso(since), _utc_iso(until)),
        )
        return await cur.fetchone() is not None


def _utc_iso(value: datetime) -> str:
    """Clave de checkpoint canónica: el mismo instante con otro offset debe
    matchear el mismo tramo. Naive se rechaza (regla del proyecto: UTC tz-aware)."""
    if value.utcoffset() is None:
        raise ValueError("los límites de sub-ventana deben ser timezone-aware (UTC)")
    return value.astimezone(UTC).isoformat()
