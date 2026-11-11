"""La cola de capturas por HTTP (puntos 7, 9 y 10 de docs/PLAN.md).

La página local, el popup y la CLI encolan; la extensión pide la siguiente con
`POST /captures/next`, la crawlea y avisa su estado. Cuando una termina, el servicio exporta
su CSV y manda una notificación del sistema; un corte también avisa, con su motivo.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import FileResponse

from tweet_extractor.domain.account import AccountCheck, AccountStatus
from tweet_extractor.i18n import Note, number, t
from tweet_extractor.mappers.account_mapper import map_account
from tweet_extractor.mappers.base import MapperError
from tweet_extractor.service.routers import LangDep, SvcDep
from tweet_extractor.service.schemas import (
    BrowserOut,
    CaptureCreate,
    CaptureOut,
    CaptureReport,
    GateDay,
)
from tweet_extractor.storage.csv_exporter import export_account
from tweet_extractor.storage.queue import Capture, CaptureState

router = APIRouter()
log = logging.getLogger(__name__)

# Cuánto de la respuesta va al log cuando x.com cambia el formato de `UserByScreenName`.
_EXAMPLE_CHARS = 2000

EMPTY_NOTE = Note(key="note.empty")

# Por qué una captura no trajo ni un tweet, según cómo ve X a la cuenta (punto 10).
ACCOUNT_NOTES = {
    AccountStatus.ACTIVE: "note.account_quiet",
    AccountStatus.PROTECTED: "note.account_protected",
    AccountStatus.SUSPENDED: "note.account_suspended",
    AccountStatus.NOT_FOUND: "note.account_not_found",
    AccountStatus.BLOCKED: "note.account_blocked",
    AccountStatus.UNAVAILABLE: "note.account_unavailable",
}


def _account(raw: dict[str, Any] | None, account: str) -> AccountCheck | None:
    """El estado de la cuenta según la respuesta de `UserByScreenName`, si la extensión la
    mandó y se entiende. Si x.com cambió el formato, queda en el log y el aviso es el de
    siempre: nunca se afirma que una cuenta no existe por no entender la respuesta."""
    if raw is None:
        return None
    try:
        return map_account(raw)
    except MapperError as exc:
        log.warning(
            "x.com cambió el formato de UserByScreenName (@%s): %s. Ejemplo: %s",
            account,
            exc,
            json.dumps(raw, ensure_ascii=False)[:_EXAMPLE_CHARS],
        )
        return None


def empty_note(account: str, check: AccountCheck | None) -> Note:
    """El aviso de una captura sin un solo tweet en el rango."""
    if check is None:
        return EMPTY_NOTE
    params: dict[str, str | int | Note] = {"account": account}
    if check.status == AccountStatus.UNAVAILABLE:
        params["reason"] = check.reason or "?"
    return Note(key=ACCOUNT_NOTES[check.status], params=params)


def _utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _percent(cap: Capture, oldest: datetime | None) -> int:
    """Qué parte del rango ya cubre la captura: va del más nuevo al más viejo, así que lo
    cubierto es de `oldest` a `until`, al segundo (un rango de un día también avanza)."""
    if cap.state == CaptureState.DONE:
        return 100
    if oldest is None:
        return 0
    until = _utc(cap.until)
    total = (until - _utc(cap.since)).total_seconds()
    covered = (until - oldest).total_seconds()
    return max(0, min(99, int(covered * 100 // total)))


async def _out(svc: SvcDep, cap: Capture) -> CaptureOut:
    oldest_dt, captured = await svc.store.account_extent(
        cap.account, since=_utc(cap.since), until=_utc(cap.until)
    )
    oldest = oldest_dt.date() if oldest_dt else None
    return CaptureOut(
        id=cap.id,
        account=cap.account,
        since=cap.since,
        until=cap.until,
        encoding=cap.encoding,
        delimiter=cap.delimiter,
        state=cap.state,
        note=Note.load(cap.note),
        created_at=cap.created_at,
        started_at=cap.started_at,
        finished_at=cap.finished_at,
        last_page_at=cap.last_page_at,
        resume_at=cap.resume_at,
        csv=cap.csv,
        download_url=f"/captures/{cap.id}/csv" if cap.csv else None,
        exported=cap.exported,
        excluded_replies=cap.excluded_replies,
        oldest=oldest,
        captured=captured,
        percent=_percent(cap, oldest_dt),
    )


async def _finish(svc: SvcDep, cap: Capture, check: AccountCheck | None = None) -> Capture:
    """Exporta el CSV de una captura que terminó y avisa. Sin un solo tweet en el rango no
    hay CSV que valga: se dice por qué, según cómo ve X a la cuenta si se sabe (punto 10)."""
    since, until = _utc(cap.since), _utc(cap.until)
    _, total = await svc.store.account_extent(cap.account, since=since, until=until)
    title = t("notify.done", account=cap.account)
    if total == 0:
        note = empty_note(cap.account, check)
        cap = await svc.queue.record_export(
            cap.id, csv=None, exported=0, excluded_replies=0, note=note.dump()
        )
        svc.notifier.post(title, note.render())
        return cap
    path, exported = await export_account(
        svc.store,
        cap.account,
        svc.settings.captures_dir,
        since=since,
        until=until,
        encoding=cap.encoding,
        delimiter=cap.delimiter,
    )
    cap = await svc.queue.record_export(
        cap.id, csv=path.name, exported=exported, excluded_replies=max(0, total - exported)
    )
    svc.notifier.post(title, t("notify.done_body", count=number(exported), file=path.name), path)
    return cap


@router.post("/captures", response_model=list[CaptureOut], status_code=status.HTTP_201_CREATED)
async def enqueue(body: CaptureCreate, svc: SvcDep) -> list[CaptureOut]:
    """Encola una captura por cuenta. La misma cuenta y rango ya en cola no se repite."""
    caps = await svc.queue.add(
        body.accounts, body.since, body.until, encoding=body.encoding, delimiter=body.delimiter
    )
    if svc.runner is not None:
        svc.runner.wake()
    return [await _out(svc, c) for c in caps]


@router.get("/captures", response_model=list[CaptureOut])
async def list_captures(svc: SvcDep) -> list[CaptureOut]:
    """Las últimas capturas, de la más nueva a la más vieja, con su avance."""
    return [await _out(svc, c) for c in await svc.queue.recent()]


@router.post(
    "/captures/next",
    response_model=CaptureOut,
    responses={204: {"description": "No hay nada que capturar"}},
)
async def next_capture(
    svc: SvcDep, x_xport_runner: Annotated[str | None, Header()] = None
) -> CaptureOut | Response:
    """La captura que tiene que crawlear la extensión: la que está en curso o, si no hay, la
    siguiente de la cola, que pasa a en curso (por eso es POST). Con el tope de 24 h lleno
    no empieza ninguna nueva (regla 1 de docs/PLAN.md); 204 si no hay nada. Con el navegador
    de xport en marcha (punto 13), la cola es solo suya: la extensión de otro navegador
    recibe 204 y no abre pestañas."""
    if svc.runner is not None and not svc.runner.accepts(x_xport_runner):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    cap = await svc.queue.active()
    if cap is None and await svc.gate.remaining() > 0:
        cap = await svc.queue.claim_next()
    if cap is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await _out(svc, cap)


@router.post("/captures/{capture_id}/state", response_model=CaptureOut)
async def report_state(
    capture_id: int, body: CaptureReport, svc: SvcDep, lang: LangDep
) -> CaptureOut:
    """Lo que avisa la extensión (en curso, en pausa, esperando a X, terminada, cortada) o
    la persona al cancelarla. Terminada: el servicio exporta el CSV y lo notifica; si no hubo
    ni un tweet, el aviso dice por qué según el `UserByScreenName` que manda la extensión.
    Cortada: se notifica el motivo, salvo que la haya cortado la persona. Esperando a X no se
    notifica: sigue sola."""
    note = body.note.dump() if isinstance(body.note, Note) else None
    cap, changed = await svc.queue.set_state(
        capture_id, CaptureState(body.state), note, body.resume_at
    )
    if cap is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=t("error.capture_not_found", lang, id=capture_id)
        )
    if changed and cap.state == CaptureState.DONE:
        cap = await _finish(svc, cap, _account(body.user, cap.account))
    elif changed and cap.state == CaptureState.STOPPED and not body.by_user:
        why = Note.load(cap.note)
        svc.notifier.post(
            t("notify.stopped", account=cap.account),
            why.render() if why else t("notify.see_page"),
        )
    return await _out(svc, cap)


@router.get("/captures/{capture_id}/csv")
async def capture_csv(capture_id: int, svc: SvcDep, lang: LangDep) -> FileResponse:
    """El CSV de una captura terminada."""
    cap = await svc.queue.get(capture_id)
    if cap is None or cap.csv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=t("error.capture_no_csv", lang))
    path = svc.settings.captures_dir / cap.csv
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=t("error.csv_gone", lang))
    return FileResponse(path, media_type="text/csv", filename=cap.csv)


@router.get("/browser", response_model=BrowserOut)
async def browser_status(svc: SvcDep) -> BrowserOut:
    """En qué anda el navegador sin ventana de xport, para la página."""
    if svc.runner is None:
        return BrowserOut(state="off", note=None)
    st = svc.runner.status
    return BrowserOut(state=st.state.value, note=st.note)


@router.get("/gate/days", response_model=list[GateDay])
async def gate_days(svc: SvcDep) -> list[GateDay]:
    """Lo visto por día (hora local) en los últimos 30 días con actividad. Es historia: el
    tope es la ventana deslizante de `GET /gate`."""
    return [GateDay(day=date.fromisoformat(day), accessed=n) for day, n in await svc.gate.daily(30)]
