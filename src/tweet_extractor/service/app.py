from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.config import Settings
from tweet_extractor.service.ingest import router as ingest_router
from tweet_extractor.service.jobs import (
    BackendBuilder,
    JobRegistry,
    ServiceState,
    build_backend,
)
from tweet_extractor.service.routers import router
from tweet_extractor.storage.sqlite_store import SqliteStore


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
    # CORS lo maneja FastAPI, NUNCA Nginx (regla de CLAUDE.md: no duplicar headers).
    # Sin orígenes configurados no se monta el middleware (default seguro).
    if cfg.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router)
    app.include_router(ingest_router)
    return app


# Entry-point para uvicorn (`tweet_extractor.service.app:app`, ver Dockerfile).
app = create_app()
