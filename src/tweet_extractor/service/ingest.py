from __future__ import annotations

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
    ProgressResult,
    ResetRequest,
    ResetResult,
)
from tweet_extractor.storage.csv_exporter import export_account

router = APIRouter()


@router.post("/ingest", response_model=IngestResult)
async def ingest(body: IngestPayload, svc: SvcDep) -> IngestResult:
    """Recibe páginas GraphQL capturadas in-page (patrón C) y las normaliza con el
    pipeline existente: extract (envelope) -> map (RT fuera, quotes sí) -> store.save
    (dedup por id) -> gate.record (record-after: el acceso ya ocurrió en el browser).

    Contabilidad record-after (ver spec §5): si el ledger global YA está en el tope,
    se rechaza (429) — no aceptamos más. Pero la ingesta que CRUZA el tope SÍ se
    procesa y registra (el acceso ya pasó; el ledger debe quedar verídico) y se marca
    `over_cap`; las siguientes caen en el 429. El ledger NO deduplica."""
    if await svc.gate.remaining() <= 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="tope global de 24 h alcanzado: ingesta pausada",
        )
    mapped: list[MappedTweet] = []
    accessed = captured = 0
    for page in body.pages:
        accessed += count_accessed(page)
        for raw in extract_tweet_results(page):
            captured += 1
            try:
                m = map_tweet(raw, account=body.account)
            except MapperError:
                continue  # tweet embebido/roto: se descarta sin tumbar la página
            if m is not None:
                mapped.append(m)
    saved = await svc.store.save(mapped)
    # Piso defensivo (mismo criterio que GatedProvider): el ledger nunca debe contar
    # MENOS que los tweets de nivel-tope efectivamente extraídos. Si `count_accessed`
    # sub-cuenta (p.ej. un __typename no reconocido que igual mapea), sub-contar es la
    # dirección INSEGURA del cap (regla #1); nos quedamos con el máximo.
    accessed = max(accessed, captured)
    # accessed==0 (página vacía/malformada) -> no se registra nada; solo se reporta el uso.
    usage = await svc.gate.record(accessed) if accessed else await svc.gate.usage()
    remaining = svc.gate.hard_cap - usage
    return IngestResult(
        account=body.account,
        captured=captured,
        saved=saved,
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
    )
    # Capturados en el rango que NO entran al CSV = replies a terceros (la política deja
    # self-threads y descarta conversaciones ajenas). `total_en_rango - exportados`: el
    # total cuenta TODO lo guardado en rango; `exported` ya aplicó la política.
    _, total_in_range, _ = await svc.store.account_extent(
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
async def progress(body: ExportRequest, svc: SvcDep) -> ProgressResult:
    """Frente de reanudación: el tweet más viejo capturado de una cuenta en
    `[since, until)` (y cuántos hay). La extensión reanuda la búsqueda desde ese día
    en vez de re-pedir desde `until`. La captura va del más nuevo al más viejo, así
    que el más viejo guardado = hasta dónde se llegó; como x.com frena la búsqueda a
    ~1000 y obliga a volver más tarde, retomar no debe regastar ese cupo."""
    oldest, captured, oldest_day = await svc.store.account_extent(
        body.account, since=body.since_utc(), until=body.until_utc()
    )
    return ProgressResult(
        account=body.account,
        oldest=oldest.date() if oldest else None,
        captured=captured,
        oldest_count=oldest_day,
    )


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
