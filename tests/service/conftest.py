from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tweet_extractor.config import Settings
from tweet_extractor.mappers.twscrape_mapper import map_tweet
from tweet_extractor.providers.base import Page, RawTweet, SearchQuery, TweetProvider
from tweet_extractor.providers.factory import Backend
from tweet_extractor.service.app import create_app
from tweet_extractor.service.jobs import BackendBuilder


class FakeProvider(TweetProvider):
    """Provider en memoria: entrega `tweets` en la primera página de cada tramo
    (cursor None) y nada después. `delay` mantiene el job en RUNNING para poder
    testear estados intermedios (CSV pedido antes de terminar). `accessed_count`
    = todos los objetos de la página, así el gate cuenta también lo descartado."""

    max_accessed_per_page = 60

    def __init__(self, tweets: list[RawTweet], *, delay: float = 0.0) -> None:
        self._tweets = tweets
        self._delay = delay

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        if self._delay:
            await asyncio.sleep(self._delay)
        tweets = self._tweets if cursor is None else []
        return Page(tweets=tweets, accessed_count=len(tweets), next_cursor=None)


def fake_backend_builder(tweets: list[RawTweet], *, delay: float = 0.0) -> BackendBuilder:
    """El seam que `create_app` inyecta: un backend fake (sin twscrape ni cookies)
    aparejado con el mapper real, para ejercitar el pipeline completo offline."""

    async def _build(settings: Settings) -> Backend:
        return Backend(provider=FakeProvider(tweets, delay=delay), mapper=map_tweet)

    return _build


def make_settings(tmp_path: Path, **over: Any) -> Settings:
    params: dict[str, Any] = {
        "audit_db_path": tmp_path / "audit" / "ledger.db",
        "data_db_path": tmp_path / "tweets.db",
        "csv_dir": tmp_path / "csv",
    }
    params.update(over)
    return Settings(**params)


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[..., TestClient]:
    """Factory de TestClient con DBs temporales y un backend fake. Usar SIEMPRE
    como context manager (`with make_client(...) as client:`) para disparar el
    lifespan que abre el gate/store singletons."""

    def _make(
        tweets: list[RawTweet] | None = None, *, delay: float = 0.0, **over: Any
    ) -> TestClient:
        settings = make_settings(tmp_path, **over)
        app = create_app(settings, backend_builder=fake_backend_builder(tweets or [], delay=delay))
        return TestClient(app)

    return _make


@pytest.fixture
def wait_done() -> Callable[[TestClient, str], dict[str, Any]]:
    """Hace polling de GET /jobs/{id} hasta done/error. El loop de eventos corre
    en el portal del TestClient; `time.sleep` cede el GIL para que avance."""

    def _wait(client: TestClient, job_id: str, tries: int = 500) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for _ in range(tries):
            data = client.get(f"/jobs/{job_id}").json()
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.01)
        raise AssertionError(f"el job no terminó a tiempo: {data}")

    return _wait
