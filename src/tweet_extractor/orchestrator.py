from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tweet_extractor.mappers.base import MappedTweet, Mapper
from tweet_extractor.providers.base import SearchQuery, TweetProvider
from tweet_extractor.providers.subwindows import subwindows
from tweet_extractor.storage.csv_exporter import (
    DEFAULT_DELIMITER,
    DEFAULT_ENCODING,
    export_account,
)
from tweet_extractor.storage.sqlite_store import SqliteStore

# Filas por tanda de INSERT: amortiza commits sin retener un tramo entero en memoria.
_SAVE_BATCH = 200


@dataclass(frozen=True)
class AccountResult:
    """Resumen de una cuenta al cierre del job."""

    account: str
    csv_path: Path
    exported: int  # filas del CSV (ya con la política de replies aplicada)
    saved: int  # tweets NUEVOS persistidos en este run (lo dedupeado no cuenta)
    windows_run: int
    windows_skipped: int  # tramos salteados por checkpoint (ya completos de un run previo)


async def run_job(
    accounts: Sequence[str],
    since: datetime,
    until: datetime,
    *,
    provider: TweetProvider,
    mapper: Mapper,
    store: SqliteStore,
    out_dir: Path,
    subwindow_days: int = 7,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
    log: Callable[[str], None] = lambda _msg: None,
) -> list[AccountResult]:
    """El loop orquestador de la Fase 1: por cuenta, un `SearchQuery` por tramo de
    `subwindows()` -> `provider.fetch_tweets` -> `map_tweet` -> `store.save` por
    tandas -> checkpoint al CERRAR el tramo -> `export_account` al final (ahí se
    aplica la política de replies, que necesita el job completo de la cuenta).

    `provider` DEBE ser el `GatedProvider` (regla #1 de CLAUDE.md): run_job no
    hace ningún fetch por fuera de él, así que todo acceso pasa por el gate. Los
    tramos ya checkpointeados se saltean SIN tocar al provider (cero presupuesto).
    Un fallo a mitad de tramo propaga sin marcar el checkpoint: el re-run repite
    ese tramo entero y el dedupe por PK absorbe lo ya guardado.

    Falla rápido: el primer error (provider/mapper/compliance) aborta el job;
    los CSV de las cuentas ya cerradas quedan escritos.
    """
    results: list[AccountResult] = []
    for account in accounts:
        saved = windows_run = windows_skipped = 0
        for w_since, w_until in subwindows(since, until, subwindow_days):
            tramo = f"[{account}] {w_since:%Y-%m-%d} → {w_until:%Y-%m-%d}"
            if await store.is_window_done(account, w_since, w_until):
                windows_skipped += 1
                log(f"{tramo}: ya completado, se saltea")
                continue
            saved_tramo = await _run_window(account, w_since, w_until, provider, mapper, store)
            await store.mark_window_done(account, w_since, w_until)
            saved += saved_tramo
            windows_run += 1
            log(f"{tramo}: {saved_tramo} tweets nuevos")
        csv_path, exported = await export_account(
            store, account, out_dir, encoding=encoding, delimiter=delimiter
        )
        results.append(
            AccountResult(
                account=account,
                csv_path=csv_path,
                exported=exported,
                saved=saved,
                windows_run=windows_run,
                windows_skipped=windows_skipped,
            )
        )
        log(f"[{account}] exportado: {exported} tweets → {csv_path}")
    return results


async def _run_window(
    account: str,
    w_since: datetime,
    w_until: datetime,
    provider: TweetProvider,
    mapper: Mapper,
    store: SqliteStore,
) -> int:
    """Fetchea y persiste UN tramo. Devuelve cuántos tweets nuevos guardó."""
    query = SearchQuery(username=account, since=w_since, until=w_until)
    saved = 0
    batch: list[MappedTweet] = []
    async for raw in provider.fetch_tweets(query):
        mapped = mapper(raw, account=account)
        if mapped is None:  # RT/tombstone: descarte esperado (ya contó en el gate)
            continue
        batch.append(mapped)
        if len(batch) >= _SAVE_BATCH:
            saved += await store.save(batch)
            batch.clear()
    saved += await store.save(batch)
    return saved
