from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from tests.providers._fixtures import search_response, tweet_entry, tweet_result
from tests.service.conftest import make_settings
from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.service.app import create_app


def post_ingest(client: TestClient, pages: list[dict], account: str = "someuser"):
    return client.post("/ingest", json={"account": account, "op": "SearchTimeline", "pages": pages})


def test_ingest_persiste_y_registra_en_gate(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response([tweet_entry("1"), tweet_entry("2")])
        r = post_ingest(client, [page])
        assert r.status_code == 200
        body = r.json()
        assert body["captured"] == 2
        assert body["saved"] == 2
        assert body["accessed"] == 2
        assert body["gate_usage"] == 2  # el gate (record-after) contó los accesos
        assert body["over_cap"] is False
        # el CSV se exporta desde lo ingerido (gate sigue en 2):
        assert client.get("/gate").json()["usage"] == 2


def test_ingest_rt_se_descarta_pero_cuenta(tmp_path: Path) -> None:
    # Regla #1 por el camino in-page: el RT no se persiste pero su acceso igual cuenta.
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response([tweet_entry("1"), tweet_entry("2", retweeted=tweet_result("rt"))])
        body = post_ingest(client, [page]).json()
        assert body["captured"] == 2  # 2 entries de nivel-tope
        assert body["saved"] == 1  # el RT lo descarta el mapper
        assert body["accessed"] == 3  # citante "1" + citante "2" + RT embebido
        assert body["gate_usage"] == 3


def test_ingest_dedup_por_id(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response([tweet_entry("1")])
        assert post_ingest(client, [page]).json()["saved"] == 1
        body2 = post_ingest(client, [page]).json()
        assert body2["saved"] == 0  # mismo id: dedup en el store
        assert body2["gate_usage"] == 2  # pero el ledger NO deduplica (cuenta de nuevo)


def test_ingest_pagina_malformada_no_crashea(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        body = post_ingest(client, [{}]).json()
        assert body["captured"] == 0
        assert body["saved"] == 0
        assert body["accessed"] == 0
        assert body["gate_usage"] == 0  # accessed=0 -> no se llama record


def test_ingest_handle_se_limpia(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        body = post_ingest(client, [search_response([tweet_entry("1")])], account="  @nasa ").json()
        assert body["account"] == "nasa"


def test_ingest_over_cap(tmp_path: Path) -> None:
    # record-after: la ingesta que CRUZA el cap pasa (200, over_cap=True; el acceso ya
    # ocurrió, se registra veraz); la SIGUIENTE se rechaza con 429.
    settings = make_settings(tmp_path)

    async def prefill() -> None:
        async with SlidingWindowGate(
            settings.audit_db_path, settings.hard_cap, settings.window_s
        ) as g:
            await g.record(settings.hard_cap - 1)  # remaining = 1 al arrancar

    asyncio.run(prefill())

    with TestClient(create_app(settings)) as client:
        page = search_response([tweet_entry("1"), tweet_entry("2")])  # accessed = 2
        first = post_ingest(client, [page])
        assert first.status_code == 200
        assert first.json()["over_cap"] is True
        assert first.json()["gate_remaining"] < 0

        second = post_ingest(client, [search_response([tweet_entry("3")])])
        assert second.status_code == 429
