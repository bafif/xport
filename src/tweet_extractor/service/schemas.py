from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from tweet_extractor.orchestrator import AccountResult
from tweet_extractor.service.jobs import JobRecord, JobStatus
from tweet_extractor.storage.csv_exporter import DEFAULT_DELIMITER, DEFAULT_ENCODING


def _clean_handle(raw: str) -> str:
    """Normaliza un handle: whitespace PRIMERO, después el `@` (un " @nasa" debe
    quedar "nasa"). Rechaza vacío y separadores de path (el handle se usa como
    nombre de archivo del CSV y segmento de URL). Fuente única para job + ingesta."""
    handle = raw.strip().lstrip("@").strip()
    if not handle:
        raise ValueError("el handle no puede ser vacío")
    if any(sep in handle for sep in ("/", "\\", "..")):
        raise ValueError("handle inválido (no puede contener '/', '\\' ni '..')")
    return handle


class _DateRange(BaseModel):
    """Rango `[since, until)` en `YYYY-MM-DD` (UTC, granularidad por día como en la
    CLI; `since` inclusiva, `until` exclusiva). Base compartida por los bodies que
    toman un rango (job + export): la regla del rango vive en UN solo lugar."""

    since: date
    until: date

    @model_validator(mode="after")
    def _range_ordenado(self) -> Self:
        if self.since >= self.until:
            raise ValueError("since debe ser anterior a until")
        return self

    def since_utc(self) -> datetime:
        return datetime(self.since.year, self.since.month, self.since.day, tzinfo=UTC)

    def until_utc(self) -> datetime:
        return datetime(self.until.year, self.until.month, self.until.day, tzinfo=UTC)


class JobCreate(_DateRange):
    """Body de `POST /jobs`."""

    accounts: list[str] = Field(min_length=1, description="Handles (sin @). Al menos uno.")
    subwindow_days: int | None = Field(default=None, ge=1)
    encoding: str = DEFAULT_ENCODING
    delimiter: str = DEFAULT_DELIMITER

    @field_validator("accounts")
    @classmethod
    def _clean_accounts(cls, raw: list[str]) -> list[str]:
        # Dedup preservando orden: la misma cuenta dos veces no debe duplicar el CSV.
        return list(dict.fromkeys(_clean_handle(a) for a in raw))


class AccountResultDTO(BaseModel):
    account: str
    csv: str  # nombre del archivo (<account>.csv)
    download_url: str  # GET de este CSV
    exported: int  # filas del CSV (con la política de replies aplicada)
    saved: int  # tweets NUEVOS persistidos en este run
    windows_run: int
    windows_skipped: int

    @classmethod
    def from_result(cls, job_id: str, result: AccountResult) -> AccountResultDTO:
        return cls(
            account=result.account,
            csv=result.csv_path.name,
            download_url=f"/jobs/{job_id}/csv/{result.account}",
            exported=result.exported,
            saved=result.saved,
            windows_run=result.windows_run,
            windows_skipped=result.windows_skipped,
        )


class JobSummary(BaseModel):
    """Vista liviana para `GET /jobs` (sin log ni resultados detallados)."""

    id: str
    status: JobStatus
    accounts: list[str]
    since: datetime
    until: datetime
    created_at: datetime
    finished_at: datetime | None
    error: str | None

    @classmethod
    def from_record(cls, record: JobRecord) -> JobSummary:
        return cls(
            id=record.id,
            status=record.status,
            accounts=record.accounts,
            since=record.since,
            until=record.until,
            created_at=record.created_at,
            finished_at=record.finished_at,
            error=record.error,
        )


class JobResponse(JobSummary):
    """Vista completa para `GET /jobs/{id}`: suma el rastro de progreso y, al
    terminar, el resumen por cuenta con la URL de descarga de cada CSV."""

    log: list[str]
    results: list[AccountResultDTO] | None

    @classmethod
    def from_record(cls, record: JobRecord) -> JobResponse:
        results = (
            [AccountResultDTO.from_result(record.id, r) for r in record.results]
            if record.results is not None
            else None
        )
        return cls(
            id=record.id,
            status=record.status,
            accounts=record.accounts,
            since=record.since,
            until=record.until,
            created_at=record.created_at,
            finished_at=record.finished_at,
            error=record.error,
            log=record.log,
            results=results,
        )


class GateResponse(BaseModel):
    """Estado del Compliance Gate: uso y capacidad restante de la ventana
    deslizante de 24 h (la invariante crítica, hecha observable)."""

    usage: int
    remaining: int
    hard_cap: int
    window_s: int


class IngestPayload(BaseModel):
    """Body de `POST /ingest`: páginas GraphQL crudas capturadas in-page (patrón C).
    La extensión NO parsea; el backend reusa los walkers de envelope + el mapper."""

    account: str
    op: str = Field(min_length=1, description="Operación capturada (p.ej. SearchTimeline)")
    pages: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("account")
    @classmethod
    def _clean(cls, raw: str) -> str:
        return _clean_handle(raw)


class IngestResult(BaseModel):
    account: str
    captured: int  # tweet_results de nivel-tope extraídos de las páginas
    saved: int  # nuevos persistidos (dedup por id en el store)
    accessed: int  # objetos-tweet contados para el gate (incluye RT/quotes)
    gate_usage: int
    gate_remaining: int
    over_cap: bool  # el ledger global cruzó el tope (las próximas ingestas dan 429)


class ExportRequest(_DateRange):
    """Body de `POST /export`: exporta a CSV lo capturado de una cuenta en un rango.
    El store acumula varias navegaciones; el filtro de fecha acota a lo pedido."""

    account: str

    @field_validator("account")
    @classmethod
    def _clean(cls, raw: str) -> str:
        return _clean_handle(raw)


class ExportResult(BaseModel):
    account: str
    csv: str  # nombre del archivo
    download_url: str  # GET de descarga (/exports/<file>)
    exported: int  # filas del CSV (con la política de replies + filtro de rango)
    # Capturados en el rango pero NO exportados: respuestas a conversaciones ajenas
    # (la política deja self-threads, descarta replies a terceros). Explica por qué el
    # CSV trae menos que el contador de "capturado" — para aclararlo en la UI.
    excluded_replies: int


class ProgressResult(BaseModel):
    """Frente de reanudación de la captura in-page (patrón C): hasta dónde se llegó
    en `[since, until)`. La extensión retoma la búsqueda desde `oldest` en vez de
    re-pedir desde `until` (x.com corta a ~1000 y hay que volver más tarde)."""

    account: str
    oldest: date | None  # día del tweet más viejo capturado en el rango (None si no hay)
    captured: int  # cuántos tweets hay guardados en el rango
    oldest_count: int  # cuántos caen en el DÍA del más viejo (alto ⇒ día saturado, ~tope de X)


class ResetRequest(BaseModel):
    """Body de `POST /reset`: borra lo capturado de una cuenta para empezar de cero.
    Necesario cuando un tweet viejo colado dejó el frente de `/progress` trabado en el
    pasado y la captura no vuelve a traer los nuevos."""

    account: str

    @field_validator("account")
    @classmethod
    def _clean(cls, raw: str) -> str:
        return _clean_handle(raw)


class ResetResult(BaseModel):
    account: str
    deleted: int  # tweets borrados de la caché de datos (el ledger del gate NO se toca)
