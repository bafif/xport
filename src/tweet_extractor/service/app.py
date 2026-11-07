from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.config import Settings
from tweet_extractor.datadir import migrate_legacy_data
from tweet_extractor.service.ingest import router as ingest_router
from tweet_extractor.service.jobs import (
    BackendBuilder,
    JobRegistry,
    ServiceState,
    build_backend,
)
from tweet_extractor.service.routers import SERVICE_NAME, router
from tweet_extractor.storage.sqlite_store import SqliteStore

log = logging.getLogger(__name__)


class AnotherServiceError(RuntimeError):
    """Ya hay otro xport corriendo: dos servicios serían dos registros del tope."""


async def find_running_xport(ports: Iterable[int], wait_s: float = 0.5) -> str | None:
    """La dirección de otro xport que ya responda en alguno de `ports`, o `None`. Corre en
    el lifespan, antes de que uvicorn abra el puerto propio: lo que responde es otro
    proceso. Se reconoce por `/healthz`, que desde noviembre de 2026 dice `service`."""
    async with httpx.AsyncClient(timeout=wait_s, trust_env=False) as client:
        for port in ports:
            try:
                r = await client.get(f"http://127.0.0.1:{port}/healthz")
                body = r.json() if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                continue
            if isinstance(body, dict) and body.get("service") == SERVICE_NAME:
                return f"http://localhost:{port}"
    return None


def create_app(
    settings: Settings | None = None,
    *,
    backend_builder: BackendBuilder = build_backend,
) -> FastAPI:
    """Factory de la app. `settings`/`backend_builder` son inyectables para los
    tests (DBs temporales + un backend fake sin twscrape ni cookies). El gate y el
    store se abren en el lifespan como SINGLETONS de la app: un único ledger global
    (regla #1) y una sola conexión de datos compartida por todos los jobs."""
    cfg = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Regla 1 de docs/PLAN.md: un solo registro del tope. Si ya corre otro xport (a
        # mano, por el native host o el compose), este no arranca: tendría su propio
        # registro o pisaría el del otro.
        other = await find_running_xport(cfg.xport_peer_ports)
        if other is not None:
            msg = (
                f"ya hay otro xport corriendo en {other}: no arranco un segundo servicio, "
                "porque dos servicios llevarían dos registros del tope de 24 h. Usá ese o "
                "bajalo antes de levantar este."
            )
            log.error(msg)
            raise AnotherServiceError(msg)
        for note in await asyncio.to_thread(migrate_legacy_data, cfg):
            log.warning("datos viejos: %s", note)
        gate = SlidingWindowGate(cfg.audit_db_path, cfg.hard_cap, cfg.window_s)
        store = SqliteStore(cfg.data_db_path)
        registry = JobRegistry()
        # setup DENTRO del try: si `store.setup()` falla, el `finally` igual cierra
        # el gate (su conexión ya abierta) — close() es no-op si no se abrió.
        try:
            await gate.setup()
            await store.setup()
            app.state.svc = ServiceState(
                settings=cfg,
                gate=gate,
                store=store,
                registry=registry,
                backend_builder=backend_builder,
            )
            yield
        finally:
            # Orden importante: cancelar los jobs en vuelo ANTES de cerrar las
            # conexiones (un run_job a mitad no debe tocar un gate/store cerrado).
            await registry.shutdown()
            await store.close()
            await gate.close()

    app = FastAPI(
        title="tweet-extractor",
        version="0.1.0",
        summary="Extrae tweets por rango de fechas → un CSV por cuenta, bajo el Compliance Gate.",
        lifespan=lifespan,
    )
    # CORS lo maneja FastAPI, NUNCA Nginx (regla 6 de docs/PLAN.md: no duplicar headers).
    # Sin orígenes configurados no se monta el middleware (default seguro).
    if cfg.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # Solo pedidos dirigidos a la propia computadora (ver `Settings.allowed_hosts`).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)
    app.include_router(router)
    app.include_router(ingest_router)
    return app


# Entry-point para uvicorn (`tweet_extractor.service.app:app`, ver deploy/Containerfile).
app = create_app()
