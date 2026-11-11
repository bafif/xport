from __future__ import annotations

import os
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from tweet_extractor.i18n import Lang, accept_lang, current, t
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


def request_lang(request: Request) -> Lang:
    """El idioma de los mensajes de la respuesta: el del `Accept-Language` (la página y la
    extensión mandan el que eligió la persona) o, sin ese header, el del servicio."""
    return accept_lang(request.headers.get("accept-language")) or current()


LangDep = Annotated[Lang, Depends(request_lang)]

# Lo que `/healthz` dice ser: así un servicio nuevo reconoce que ya corre otro xport
# (ver `find_running_xport` en app.py).
SERVICE_NAME = "xport"

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str | int]:
    """Que el servicio anda y es xport. `pid` es el proceso que atiende el puerto: con eso lo
    cierra `xport stop` (launcher.py), lo haya arrancado quien sea."""
    return {"status": "ok", "service": SERVICE_NAME, "pid": os.getpid()}


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
async def get_job(job_id: str, svc: SvcDep, lang: LangDep) -> JobResponse:
    record = svc.registry.get(job_id)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=t("error.job_not_found", lang, id=repr(job_id))
        )
    return JobResponse.from_record(record)


@router.get("/jobs/{job_id}/csv/{account}")
async def download_csv(job_id: str, account: str, svc: SvcDep, lang: LangDep) -> FileResponse:
    """Descarga el CSV de UNA cuenta del job (siempre es un archivo por
    cuenta). El job tiene que estar `done`. Sirve la ruta REAL escrita por el job
    (incluye el rango en el nombre), así el endpoint no depende del naming."""
    record = svc.registry.get(job_id)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=t("error.job_not_found", lang, id=repr(job_id))
        )
    if record.status != JobStatus.DONE or record.results is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=t("error.job_not_ready", lang, status=record.status),
        )
    result = next((r for r in record.results if r.account == account), None)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=t("error.job_no_csv", lang, account=repr(account)),
        )
    if not result.csv_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=t("error.csv_not_found", lang))
    return FileResponse(result.csv_path, media_type="text/csv", filename=result.csv_path.name)
