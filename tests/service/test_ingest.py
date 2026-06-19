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


def test_ingest_no_subcuenta_el_gate(tmp_path: Path) -> None:
    # count_accessed cuenta por __typename; si un result no lo trae pero igual mapea,
    # el ledger NO debe sub-contar (dirección insegura, regla #1): se pisa al nº de
    # tweets de nivel-tope extraídos (mismo piso que GatedProvider).
    result = {
        "rest_id": "9",
        "legacy": {
            "full_text": "sin typename",
            "created_at": "Wed Jan 04 00:00:00 +0000 2023",
            "conversation_id_str": "9",
        },
    }
    entry = {
        "entryId": "tweet-9",
        "content": {"itemContent": {"tweet_results": {"result": result}}},
    }
    with TestClient(create_app(make_settings(tmp_path))) as client:
        body = post_ingest(client, [search_response([entry])]).json()
        assert body["captured"] == 1
        assert body["saved"] == 1
        assert body["accessed"] == 1  # piso: max(count_accessed=0, captured=1)
        assert body["gate_usage"] == 1


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


def test_export_tras_ingest(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response(
            [
                tweet_entry("1"),  # default Jan 04 (en rango)
                tweet_entry("2"),  # default Jan 04 (en rango)
                tweet_entry("3", created_at="Sat Mar 04 00:00:00 +0000 2023"),  # fuera de rango
            ]
        )
        post_ingest(client, [page])
        r = client.post(
            "/export", json={"account": "@someuser", "since": "2023-01-01", "until": "2023-02-01"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["account"] == "someuser"  # handle limpio
        assert body["exported"] == 2  # el de marzo queda fuera del rango
        assert body["download_url"] == "/exports/someuser_2023-01-01_2023-02-01.csv"

        csv = client.get(body["download_url"])
        assert csv.status_code == 200
        assert csv.headers["content-type"].startswith("text/csv")
        assert "texto 1" in csv.text
        assert "texto 2" in csv.text
        assert "texto 3" not in csv.text


def test_progress_sin_datos(tmp_path: Path) -> None:
    # Sin nada capturado: el frente es None y la captura arrancará desde `until`.
    with TestClient(create_app(make_settings(tmp_path))) as client:
        r = client.post(
            "/progress", json={"account": "nasa", "since": "2023-01-01", "until": "2023-02-01"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"account": "nasa", "oldest": None, "captured": 0, "oldest_count": 0}


def test_progress_devuelve_el_mas_viejo_en_rango(tmp_path: Path) -> None:
    # El frente de reanudación = MIN(created_at) dentro del rango; lo de marzo (fuera de
    # rango) no cuenta ni mueve el frente. `oldest_count` = tuits del día del más viejo.
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response(
            [
                tweet_entry("1", created_at="Tue Jan 10 00:00:00 +0000 2023"),
                tweet_entry("2", created_at="Wed Jan 04 00:00:00 +0000 2023"),  # más viejo en rango
                tweet_entry("3", created_at="Sat Mar 04 00:00:00 +0000 2023"),  # fuera de rango
            ]
        )
        post_ingest(client, [page])
        body = client.post(
            "/progress", json={"account": "@someuser", "since": "2023-01-01", "until": "2023-02-01"}
        ).json()
        assert body["account"] == "someuser"  # handle limpio
        assert body["oldest"] == "2023-01-04"  # día del más viejo dentro del rango
        assert body["captured"] == 2  # el de marzo queda afuera
        assert body["oldest_count"] == 1  # solo el id "2" cae el 2023-01-04


def test_progress_cuenta_tuits_del_dia_mas_viejo(tmp_path: Path) -> None:
    # `oldest_count` detecta un día saturado: cuenta SOLO los del día del más viejo (no
    # los de días más nuevos), para que la extensión decida si saltear ese día.
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response(
            [
                tweet_entry("a", created_at="Wed Jan 04 06:00:00 +0000 2023"),  # día del más viejo
                tweet_entry("b", created_at="Wed Jan 04 09:00:00 +0000 2023"),  # mismo día
                tweet_entry("c", created_at="Wed Jan 04 18:00:00 +0000 2023"),  # mismo día
                tweet_entry("d", created_at="Fri Jan 06 09:00:00 +0000 2023"),  # día más nuevo
            ]
        )
        post_ingest(client, [page])
        body = client.post(
            "/progress", json={"account": "someuser", "since": "2023-01-01", "until": "2023-02-01"}
        ).json()
        assert body["oldest"] == "2023-01-04"
        assert body["captured"] == 4
        assert body["oldest_count"] == 3  # a, b, c son del 2023-01-04; d (06) no


def test_export_reporta_replies_a_terceros_excluidos(tmp_path: Path) -> None:
    # El CSV deja self-threads y descarta replies a conversaciones ajenas; `excluded_replies`
    # reporta esos descartados para aclarar en la UI por qué el CSV trae menos que lo capturado.
    with TestClient(create_app(make_settings(tmp_path))) as client:
        page = search_response(
            [
                tweet_entry("1"),  # propio (no reply) -> exporta
                tweet_entry("2", in_reply_to="999", conversation_id="999"),  # reply ajeno -> fuera
            ]
        )
        post_ingest(client, [page])
        body = client.post(
            "/export", json={"account": "someuser", "since": "2023-01-01", "until": "2023-02-01"}
        ).json()
        assert body["exported"] == 1  # solo el tweet propio
        assert body["excluded_replies"] == 1  # el reply a tercero, capturado pero fuera del CSV


def test_export_rango_invertido_422(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        r = client.post(
            "/export", json={"account": "u", "since": "2023-02-01", "until": "2023-01-01"}
        )
        assert r.status_code == 422


def test_exports_filename_traversal_404(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/exports/a..b.csv").status_code == 404  # '..' rechazado por el guard
        assert client.get("/exports/nada.csv").status_code == 404  # no existe


def test_reset_borra_datos_pero_no_el_gate(tmp_path: Path) -> None:
    # "Borrar capturado": vacía los tweets de la cuenta (resetea el frente de /progress)
    # SIN tocar el ledger del gate (regla #1: borrar datos no borra accesos ya contados).
    with TestClient(create_app(make_settings(tmp_path))) as client:
        post_ingest(client, [search_response([tweet_entry("1"), tweet_entry("2")])])
        assert client.get("/gate").json()["usage"] == 2

        rng = {"since": "2023-01-01", "until": "2023-02-01"}
        prog = client.post("/progress", json={"account": "someuser", **rng}).json()
        assert prog["captured"] == 2  # hay frente de reanudación

        body = client.post("/reset", json={"account": "@someuser"}).json()
        assert body == {"account": "someuser", "deleted": 2}  # handle limpio + 2 borrados

        # El frente quedó vacío: la próxima captura arranca desde `until`.
        prog2 = client.post("/progress", json={"account": "someuser", **rng}).json()
        assert prog2["captured"] == 0
        assert prog2["oldest"] is None
        # El ledger del gate NO se resetea: los accesos ya contados siguen.
        assert client.get("/gate").json()["usage"] == 2


def test_reset_cuenta_sin_datos(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        body = client.post("/reset", json={"account": "nadie"}).json()
        assert body == {"account": "nadie", "deleted": 0}
