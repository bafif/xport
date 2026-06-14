from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tweet_extractor.mappers.base import MappedTweet, MapperError
from tweet_extractor.mappers.twscrape_mapper import map_tweet
from tweet_extractor.providers.search_envelope import count_accessed, extract_tweet_results
from tweet_extractor.service.routers import SvcDep
from tweet_extractor.service.schemas import IngestPayload, IngestResult

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
        over_cap=remaining < 0,
    )
