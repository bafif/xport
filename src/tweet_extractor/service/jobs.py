from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.orchestrator import AccountResult, run_job
from tweet_extractor.providers.factory import Backend, build_backend
from tweet_extractor.storage.csv_exporter import DEFAULT_DELIMITER, DEFAULT_ENCODING
from tweet_extractor.storage.sqlite_store import SqliteStore

# El seam de construcción del backend (scraping ↔ oficial). Inyectable en
# `create_app` para los tests, que pasan un `Backend(FakeProvider, map_tweet)`
# sin tocar twscrape ni necesitar cookies.
BackendBuilder = Callable[[Settings], Awaitable[Backend]]


class JobStatus(StrEnum):
    PENDING = "pending"  # creado, todavía no empezó a correr
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class JobRecord:
    """Estado mutable de un job de extracción. Vive en memoria (`JobRegistry`).
    Los DATOS del job (tweets, CSV, checkpoints) sí son durables: viven en el
    SqliteStore y en disco. Si el server se reinicia, re-enviar el mismo job
    reanuda desde los checkpoints (cero presupuesto del gate para lo ya hecho)."""

    id: str
    accounts: list[str]
    since: datetime
    until: datetime
    subwindow_days: int
    encoding: str = DEFAULT_ENCODING
    delimiter: str = DEFAULT_DELIMITER
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    error: str | None = None
    # Rastro de progreso: una línea por sub-ventana (el `log` de `run_job`). Es la
    # "progreso por sub-ventana" que pide el plan, observable vía GET /jobs/{id}.
    log: list[str] = field(default_factory=list)
    results: list[AccountResult] | None = None  # solo cuando status == DONE

    def add_log(self, message: str) -> None:
        self.log.append(message)


@dataclass
class ServiceState:
    """Lo que la app comparte entre requests. El gate y el store son SINGLETONS:
    un único ledger global (el cap de ToS es por cliente, no por request) y una
    sola conexión de datos. Se montan en `app.state` y se inyectan por Depends."""

    settings: Settings
    gate: SlidingWindowGate
    store: SqliteStore
    registry: JobRegistry
    backend_builder: BackendBuilder


class JobRegistry:
    """Índice en memoria de jobs + tracking de sus tasks de background, para poder
    cancelarlas en el shutdown ANTES de cerrar el gate/store."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def create(
        self,
        accounts: Sequence[str],
        since: datetime,
        until: datetime,
        *,
        subwindow_days: int,
        encoding: str,
        delimiter: str,
    ) -> JobRecord:
        record = JobRecord(
            id=uuid.uuid4().hex,
            accounts=list(accounts),
            since=since,
            until=until,
            subwindow_days=subwindow_days,
            encoding=encoding,
            delimiter=delimiter,
        )
        self._jobs[record.id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def all(self) -> list[JobRecord]:
        # Más nuevos primero.
        return sorted(self._jobs.values(), key=lambda r: r.created_at, reverse=True)

    def submit(self, record: JobRecord, svc: ServiceState) -> None:
        """Dispara la ejecución en background y la trackea para el shutdown."""
        task = asyncio.create_task(run_extraction(record, svc))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """Cancela y espera a las tasks en vuelo. Debe correr ANTES de cerrar el
        gate/store: un `run_job` a mitad no debe tocar una conexión ya cerrada."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def run_extraction(record: JobRecord, svc: ServiceState) -> None:
    """La corutina de background de un job: arma el backend, lo envuelve en el
    `GatedProvider` (sobre el gate COMPARTIDO: ningún fetch saltea el cap) y corre
    el mismo `run_job` que la CLI. Captura TODO error para reportarlo vía la API
    en vez de morir en silencio en el event loop. `CancelledError` (shutdown)
    propaga: no se enmascara como un error del job."""
    record.status = JobStatus.RUNNING
    try:
        backend = await svc.backend_builder(svc.settings)
        gated = GatedProvider(backend.provider, svc.gate)
        results = await run_job(
            record.accounts,
            record.since,
            record.until,
            provider=gated,
            mapper=backend.mapper,
            store=svc.store,
            out_dir=svc.settings.csv_dir / record.id,
            subwindow_days=record.subwindow_days,
            encoding=record.encoding,
            delimiter=record.delimiter,
            log=record.add_log,
        )
    except Exception as exc:
        # Intencionalmente amplio: un job de background no debe tropezar la app.
        # El detalle queda accesible en GET /jobs/{id} (status=error + mensaje).
        record.status = JobStatus.ERROR
        record.error = f"{type(exc).__name__}: {exc}"
        record.finished_at = _now()
        return
    record.results = results
    record.status = JobStatus.DONE
    record.finished_at = _now()


__all__ = [
    "BackendBuilder",
    "JobRecord",
    "JobRegistry",
    "JobStatus",
    "ServiceState",
    "build_backend",
    "run_extraction",
]
