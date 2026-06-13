from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from tweet_extractor.service.jobs import JobStatus, ServiceState
from tweet_extractor.service.schemas import (
    GateResponse,
    JobCreate,
    JobResponse,
    JobSummary,
)


def get_svc(request: Request) -> ServiceState:
    """El estado compartido (gate/store/registry singletons) montado en el lifespan."""
    return cast(ServiceState, request.app.state.svc)


SvcDep = Annotated[ServiceState, Depends(get_svc)]

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/gate", response_model=GateResponse)
async def gate_status(svc: SvcDep) -> GateResponse:
    """Estado del Compliance Gate: uso y restante de la ventana deslizante de 24 h."""
    usage = await svc.gate.usage()
    return GateResponse(
        usage=usage,
        remaining=svc.gate.hard_cap - usage,
        hard_cap=svc.gate.hard_cap,
        window_s=svc.settings.window_s,
    )


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, svc: SvcDep) -> JobResponse:
    """Encola un job de extracción y lo corre en background. La respuesta trae el
    `id` para hacer polling en `GET /jobs/{id}`."""
    record = svc.registry.create(
        body.accounts,
        body.since_utc(),
        body.until_utc(),
        subwindow_days=body.subwindow_days or svc.settings.subwindow_days,
        encoding=body.encoding,
        delimiter=body.delimiter,
    )
    svc.registry.submit(record, svc)
    return JobResponse.from_record(record)


@router.get("/jobs", response_model=list[JobSummary])
async def list_jobs(svc: SvcDep) -> list[JobSummary]:
    return [JobSummary.from_record(r) for r in svc.registry.all()]


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, svc: SvcDep) -> JobResponse:
    record = svc.registry.get(job_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"job {job_id!r} no encontrado")
    return JobResponse.from_record(record)


@router.get("/jobs/{job_id}/csv/{account}")
async def download_csv(job_id: str, account: str, svc: SvcDep) -> FileResponse:
    """Descarga el CSV de UNA cuenta del job (un archivo por cuenta, según el
    spec). El job tiene que estar `done`. Sirve la ruta REAL escrita por el job
    (incluye el rango en el nombre), así el endpoint no depende del naming."""
    record = svc.registry.get(job_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"job {job_id!r} no encontrado")
    if record.status != JobStatus.DONE or record.results is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"el job está '{record.status}': el CSV todavía no está disponible",
        )
    result = next((r for r in record.results if r.account == account), None)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"la cuenta {account!r} no tiene CSV en este job"
        )
    if not result.csv_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="CSV no encontrado")
    return FileResponse(result.csv_path, media_type="text/csv", filename=result.csv_path.name)
