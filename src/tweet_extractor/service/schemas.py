from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from tweet_extractor.orchestrator import AccountResult
from tweet_extractor.service.jobs import JobRecord, JobStatus
from tweet_extractor.storage.csv_exporter import DEFAULT_DELIMITER, DEFAULT_ENCODING


class JobCreate(BaseModel):
    """Body de `POST /jobs`. Las fechas son `YYYY-MM-DD` (UTC, granularidad por
    día como en la CLI); `since` inclusiva, `until` exclusiva."""

    accounts: list[str] = Field(min_length=1, description="Handles (sin @). Al menos uno.")
    since: date
    until: date
    subwindow_days: int | None = Field(default=None, ge=1)
    encoding: str = DEFAULT_ENCODING
    delimiter: str = DEFAULT_DELIMITER

    @field_validator("accounts")
    @classmethod
    def _clean_accounts(cls, raw: list[str]) -> list[str]:
        # Whitespace PRIMERO, después el @ (un " @nasa" debe quedar "nasa", no "@nasa").
        cleaned = [a.strip().lstrip("@").strip() for a in raw]
        if any(not a for a in cleaned):
            raise ValueError("los handles no pueden ser vacíos")
        # Defensa en profundidad: el handle se usa como nombre de archivo del CSV
        # (<account>_<since>_<until>.csv) y como segmento de URL de descarga. Rechazar
        # separadores acá (422 al enviar) en vez de que el job falle recién al exportar.
        if any(sep in a for a in cleaned for sep in ("/", "\\", "..")):
            raise ValueError("handle inválido (no puede contener '/', '\\' ni '..')")
        # Dedup preservando orden: la misma cuenta dos veces no debe duplicar el CSV.
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def _range_ordenado(self) -> JobCreate:
        if self.since >= self.until:
            raise ValueError("since debe ser anterior a until")
        return self

    def since_utc(self) -> datetime:
        return datetime(self.since.year, self.since.month, self.since.day, tzinfo=UTC)

    def until_utc(self) -> datetime:
        return datetime(self.until.year, self.until.month, self.until.day, tzinfo=UTC)


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
