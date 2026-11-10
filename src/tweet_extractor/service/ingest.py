from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from tweet_extractor.mappers.base import MappedTweet, MapperError
from tweet_extractor.mappers.twscrape_mapper import map_tweet
from tweet_extractor.providers.search_envelope import count_accessed, extract_tweet_results
from tweet_extractor.service.routers import SvcDep
from tweet_extractor.service.schemas import (
    ExportRequest,
    ExportResult,
    IngestPayload,
    IngestResult,
    ProgressRequest,
    ProgressResult,
    ResetRequest,
    ResetResult,
)
from tweet_extractor.storage.csv_exporter import export_account

router = APIRouter()
log = logging.getLogger(__name__)

# Cuánto del tweet de ejemplo va al log cuando x.com cambia el formato.
_EXAMPLE_CHARS = 2000


@router.post("/ingest", response_model=IngestResult)
async def ingest(body: IngestPayload, svc: SvcDep) -> IngestResult:
    """Recibe páginas GraphQL capturadas in-page (patrón C) y las normaliza con el
    pipeline existente: extract (envelope) -> map (RT fuera, quotes sí) -> store.save
    (dedup por id) -> gate.record (record-after: el acceso ya ocurrió en el browser).

    Contabilidad record-after (regla 1 de docs/PLAN.md): las páginas que llegan ya se
    vieron en el navegador, así que sus accesos se registran SIEMPRE, también cuando se
    rechazan. Si el ledger global ya está en el tope, se registran y se responde 429 sin
    guardar nada: la extensión corta el crawl al recibirlo. La ingesta que CRUZA el tope
    se procesa, se registra y se marca `over_cap` (la extensión también corta ahí). El
    ledger NO deduplica."""
    raws = [raw for page in body.pages for raw in extract_tweet_results(page)]
    captured = len(raws)
    # Piso defensivo (mismo criterio que GatedProvider): el ledger nunca debe contar
    # MENOS que los tweets de nivel-tope efectivamente extraídos. Si `count_accessed`
    # sub-cuenta (p.ej. un __typename no reconocido que igual mapea), sub-contar es la
    # dirección INSEGURA del cap (regla #1); nos quedamos con el máximo.
    accessed = max(sum(count_accessed(page) for page in body.pages), captured)
    if await svc.gate.remaining() <= 0:
        if accessed:
            await svc.gate.record(accessed)  # el acceso ya pasó: rechazar no lo borra
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="tope global de 24 h alcanzado: ingesta pausada",
        )
    mapped: list[MappedTweet] = []
    rejected = 0
    example: tuple[MapperError, dict[str, Any]] | None = None
    for raw in raws:
        try:
            m = map_tweet(raw, account=body.account)
        except MapperError as e:
            # Un tweet roto se descarta sin tumbar la página, pero se cuenta: si x.com
            # cambia la forma de los datos, fallan todos y hay que notarlo.
            rejected += 1
            example = example or (e, raw)
            continue
        if m is not None:
            mapped.append(m)
    format_changed = rejected * 2 > captured
    if format_changed and example is not None:
        err, raw = example
        log.warning(
            "x.com cambió el formato de los datos: el mapper no entendió %d de %d tweets de "
            "@%s. Ejemplo (%s): %s",
            rejected,
            captured,
            body.account,
            err,
            json.dumps(raw, ensure_ascii=False)[:_EXAMPLE_CHARS],
        )
    saved = await svc.store.save(mapped)
    await svc.queue.touch(body.account)  # la hora de la última página, para la página local
    # accessed==0 (página vacía/malformada) -> no se registra nada; solo se reporta el uso.
    usage = await svc.gate.record(accessed) if accessed else await svc.gate.usage()
    remaining = svc.gate.hard_cap - usage
    return IngestResult(
        account=body.account,
        captured=captured,
        saved=saved,
        rejected=rejected,
        format_changed=format_changed,
        accessed=accessed,
        gate_usage=usage,
        gate_remaining=remaining,
        # `<= 0` coherente con el guard de entrada: "exactamente en el tope" ya está
        # over_cap (la próxima ingesta da 429).
        over_cap=remaining <= 0,
    )


@router.post("/export", response_model=ExportResult)
async def export(body: ExportRequest, svc: SvcDep) -> ExportResult:
    """Exporta a CSV lo capturado de una cuenta en `[since, until)`, on-demand
    (reusa `export_account`: política de replies + filtro de rango). Escribe en
    `captures_dir` y devuelve la URL de descarga."""
    path, exported = await export_account(
        svc.store,
        body.account,
        svc.settings.captures_dir,
        since=body.since_utc(),
        until=body.until_utc(),
        encoding=body.encoding,
        delimiter=body.delimiter,
    )
    # Capturados en el rango que NO entran al CSV = replies a terceros (la política deja
    # self-threads y descarta conversaciones ajenas). `total_en_rango - exportados`: el
    # total cuenta TODO lo guardado en rango; `exported` ya aplicó la política.
    _, total_in_range = await svc.store.account_extent(
        body.account, since=body.since_utc(), until=body.until_utc()
    )
    return ExportResult(
        account=body.account,
        csv=path.name,
        download_url=f"/exports/{path.name}",
        exported=exported,
        excluded_replies=max(0, total_in_range - exported),
    )


@router.post("/progress", response_model=ProgressResult)
async def progress(body: ProgressRequest, svc: SvcDep) -> ProgressResult:
    """Frente de reanudación: el tweet más viejo capturado de una cuenta en
    `[since, until)`, al segundo (y cuántos hay). La extensión reanuda la búsqueda
    desde ese momento en vez de re-pedir desde `until`. La captura va del más nuevo al
    más viejo, así que el más viejo guardado = hasta dónde se llegó; como X frena la
    búsqueda y obliga a volver más tarde, retomar no debe regastar ese cupo."""
    oldest, captured = await svc.store.account_extent(
        body.account, since=body.since_utc(), until=body.until_utc()
    )
    return ProgressResult(account=body.account, oldest=oldest, captured=captured)


@router.post("/reset", response_model=ResetResult)
async def reset(body: ResetRequest, svc: SvcDep) -> ResetResult:
    """Borra lo capturado de una cuenta (filas de datos + checkpoints) para empezar de
    cero. Resetea el frente de reanudación de `/progress`: un tweet viejo colado puede
    dejarlo trabado en el pasado y la captura no vuelve a traer los nuevos.

    NO toca el ledger del Compliance Gate: borrar datos NO borra accesos ya contados
    (el ledger vive en otra base y sobrevive a que se regenere ésta — regla #1)."""
    deleted = await svc.store.delete_account(body.account)
    return ResetResult(account=body.account, deleted=deleted)


@router.get("/exports/{filename}")
async def download_export(filename: str, svc: SvcDep) -> FileResponse:
    """Descarga un CSV de captura. `filename` es un único segmento; se rechazan
    separadores/`..` (defensa en profundidad ante path traversal)."""
    if any(sep in filename for sep in ("/", "\\", "..")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="archivo no encontrado")
    path = svc.settings.captures_dir / filename
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="archivo no encontrado")
    return FileResponse(path, media_type="text/csv", filename=filename)
