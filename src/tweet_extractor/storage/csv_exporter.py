from __future__ import annotations

import asyncio
import csv
import os
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import IO

from tweet_extractor.domain.models import Tweet
from tweet_extractor.mappers.twscrape_mapper import passes_reply_policy
from tweet_extractor.storage.sqlite_store import SqliteStore

# --- Defaults del CSV (ODQ resueltas con el usuario, ver CLAUDE.md) --------------
# Decididos: UTF-8 SIN BOM, delimitador coma, display de fechas en UTC. Siguen
# siendo configurables por parámetro para el caso Excel-AR (encoding="utf-8-sig"
# agrega BOM; delimiter=";" para la config regional de Excel en Argentina).
DEFAULT_ENCODING = "utf-8"
DEFAULT_DELIMITER = ","

CSV_COLUMNS = (
    "account",
    "created_at",
    "content",
    "links",
    "quoted_tweet_id",
    "quoted_tweet_url",
)

# Los links múltiples van en UNA celda separados por espacio: una URL no puede
# contener espacios literales (siempre van percent-encoded), así que el join es
# no ambiguo y reversible con .split().
LINKS_SEPARATOR = " "

# Filas por tanda de escritura: acota la memoria (streaming) y amortiza el costo
# de despachar la escritura bloqueante a un thread (nada de I/O en el event loop).
_CHUNK_ROWS = 500


def csv_filename(account: str, since: datetime, until: datetime) -> str:
    """Nombre del CSV de una cuenta: `<account>_<since>_<until>.csv` (rango pedido,
    fechas UTC). Incluir el rango deja extracciones de distintos rangos lado a lado
    sin pisarse; un re-run del MISMO rango reescribe el mismo archivo (idempotente,
    vía la escritura atómica de `write_csv`)."""
    return f"{account}_{since:%Y-%m-%d}_{until:%Y-%m-%d}.csv"


def tweet_row(tweet: Tweet) -> list[str]:
    """La fila CSV de un `Tweet`. `created_at` en ISO 8601 UTC (decidido: display en
    UTC, ya normalizado por el modelo). `content` va tal cual —saltos de línea
    incluidos— porque `QUOTE_ALL` lo mantiene en una sola fila lógica. Quote
    ausente -> celdas vacías."""
    return [
        tweet.account,
        tweet.created_at.isoformat(),
        tweet.content,
        LINKS_SEPARATOR.join(link.expanded_url for link in tweet.links),
        tweet.quoted_tweet_id or "",
        tweet.quoted_tweet_url or "",
    ]


async def write_csv(
    tweets: AsyncIterable[Tweet],
    path: Path,
    *,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> int:
    """Escribe el CSV en streaming (fila por fila, memoria acotada a `_CHUNK_ROWS`)
    con `csv` de stdlib y `QUOTE_ALL` (texto con saltos de línea/comas). Escritura
    atómica: va a `<path>.tmp` y se renombra al final — nunca queda un CSV a
    medias con nombre final (un fallo a mitad deja, a lo sumo, el `.tmp`).
    Las escrituras bloqueantes van por `asyncio.to_thread`. Devuelve cuántos
    tweets se escribieron (sin contar el header)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    handle: IO[str] = await asyncio.to_thread(tmp.open, "w", encoding=encoding, newline="")
    written = 0
    try:
        writer = csv.writer(handle, delimiter=delimiter, quoting=csv.QUOTE_ALL)
        chunk: list[list[str]] = [list(CSV_COLUMNS)]
        async for tweet in tweets:
            chunk.append(tweet_row(tweet))
            written += 1
            if len(chunk) >= _CHUNK_ROWS:
                await asyncio.to_thread(writer.writerows, chunk)
                chunk.clear()
        if chunk:
            await asyncio.to_thread(writer.writerows, chunk)
    except BaseException:
        await asyncio.to_thread(handle.close)
        await asyncio.to_thread(tmp.unlink)
        raise
    await asyncio.to_thread(handle.close)
    await asyncio.to_thread(os.replace, tmp, path)
    return written


async def export_account(
    store: SqliteStore,
    account: str,
    out_dir: Path,
    *,
    since: datetime,
    until: datetime,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> tuple[Path, int]:
    """UN CSV por cuenta: `<out_dir>/<account>_<since>_<until>.csv`, en orden
    cronológico, aplicando la política de replies en streaming (los `own_ids`
    persistidos + `passes_reply_policy` fila a fila; la colección nunca se
    materializa). Cuenta sin tweets -> CSV solo-header (señal explícita de "sin
    resultados"). Devuelve (ruta, tweets exportados)."""
    if any(sep in account for sep in ("/", "\\", "..")):
        raise ValueError(f"handle inválido para nombre de archivo: {account!r}")
    own_ids = await store.account_ids(account)

    async def filtered() -> AsyncIterator[Tweet]:
        async for mapped in store.iter_account(account, since=since, until=until):
            if passes_reply_policy(
                is_reply=mapped.is_reply,
                conversation_id=mapped.conversation_id,
                own_ids=own_ids,
            ):
                yield mapped.tweet

    path = out_dir / csv_filename(account, since, until)
    written = await write_csv(filtered(), path, encoding=encoding, delimiter=delimiter)
    return path, written
