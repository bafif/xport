from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.providers._fixtures import tweet_result
from tests.service.conftest import make_settings
from tweet_extractor.config import Settings
from tweet_extractor.providers.base import ProviderError
from tweet_extractor.providers.factory import Backend
from tweet_extractor.service.app import create_app

# Jan-01 → Jan-08 = exactamente 1 sub-ventana (default subwindow_days=7).
RANGE = {"since": "2023-01-01", "until": "2023-01-08"}

ClientFactory = Callable[..., TestClient]
WaitDone = Callable[[TestClient, str], dict[str, Any]]


def test_healthz(make_client: ClientFactory) -> None:
    with make_client() as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_gate_arranca_vacio(make_client: ClientFactory) -> None:
    with make_client() as client:
        body = client.get("/gate").json()
        assert body["usage"] == 0
        assert body["hard_cap"] == 900_000
        assert body["window_s"] == 86_400
        assert body["remaining"] == 900_000


def test_post_job_corre_y_exporta_csv(make_client: ClientFactory, wait_done: WaitDone) -> None:
    tweets = [tweet_result("1"), tweet_result("2")]
    with make_client(tweets) as client:
        r = client.post("/jobs", json={"accounts": ["@someuser"], **RANGE})
        assert r.status_code == 201
        created = r.json()
        assert created["accounts"] == ["someuser"]  # el @ se limpia
        job_id = created["id"]

        done = wait_done(client, job_id)
        assert done["status"] == "done", done
        assert done["error"] is None
        (result,) = done["results"]
        assert result["account"] == "someuser"
        assert result["exported"] == 2
        assert result["saved"] == 2
        assert result["windows_run"] == 1
        assert result["download_url"] == f"/jobs/{job_id}/csv/someuser"

        csv = client.get(result["download_url"])
        assert csv.status_code == 200
        assert csv.headers["content-type"].startswith("text/csv")
        assert "texto 1" in csv.text
        assert "texto 2" in csv.text


def test_post_job_limpia_y_dedup_accounts(make_client: ClientFactory) -> None:
    # " @nasa " -> "nasa" (whitespace ANTES del @); "@nasa" duplicado se colapsa.
    with make_client() as client:
        r = client.post("/jobs", json={"accounts": ["  @nasa ", "@nasa", "esa"], **RANGE})
        assert r.status_code == 201
        assert r.json()["accounts"] == ["nasa", "esa"]


def test_gate_cuenta_los_accesos_del_job(make_client: ClientFactory, wait_done: WaitDone) -> None:
    tweets = [tweet_result("1"), tweet_result("2")]
    with make_client(tweets) as client:
        job_id = client.post("/jobs", json={"accounts": ["someuser"], **RANGE}).json()["id"]
        wait_done(client, job_id)
        gate = client.get("/gate").json()
        assert gate["usage"] == 2  # el gate singleton contó los 2 accesos del job
        assert gate["remaining"] == 900_000 - 2


def test_rt_se_descarta_pero_cuenta_en_el_gate(
    make_client: ClientFactory, wait_done: WaitDone
) -> None:
    # Regla #1 de CLAUDE.md: el RT no se exporta, pero su ACCESO igual cuenta.
    tweets = [tweet_result("1"), tweet_result("2", retweeted=tweet_result("rt"))]
    with make_client(tweets) as client:
        job_id = client.post("/jobs", json={"accounts": ["someuser"], **RANGE}).json()["id"]
        done = wait_done(client, job_id)
        (result,) = done["results"]
        assert result["exported"] == 1  # el RT no entra al CSV
        assert client.get("/gate").json()["usage"] == 2  # pero sí contó en el gate


def test_job_inexistente_404(make_client: ClientFactory) -> None:
    with make_client() as client:
        assert client.get("/jobs/nope").status_code == 404


def test_csv_de_job_inexistente_404(make_client: ClientFactory) -> None:
    with make_client() as client:
        assert client.get("/jobs/nope/csv/someuser").status_code == 404


def test_csv_cuenta_ajena_al_job_404(make_client: ClientFactory, wait_done: WaitDone) -> None:
    with make_client([tweet_result("1")]) as client:
        job_id = client.post("/jobs", json={"accounts": ["someuser"], **RANGE}).json()["id"]
        wait_done(client, job_id)
        assert client.get(f"/jobs/{job_id}/csv/otro").status_code == 404


def test_csv_antes_de_terminar_es_409(make_client: ClientFactory) -> None:
    # El provider duerme 0.3s: el job sigue corriendo y el CSV todavía no existe.
    with make_client([tweet_result("1")], delay=0.3) as client:
        job_id = client.post("/jobs", json={"accounts": ["someuser"], **RANGE}).json()["id"]
        assert client.get(f"/jobs/{job_id}/csv/someuser").status_code == 409


def test_fechas_invertidas_422(make_client: ClientFactory) -> None:
    with make_client() as client:
        r = client.post(
            "/jobs", json={"accounts": ["u"], "since": "2023-02-01", "until": "2023-01-01"}
        )
        assert r.status_code == 422


def test_account_vacio_422(make_client: ClientFactory) -> None:
    with make_client() as client:
        assert client.post("/jobs", json={"accounts": ["@"], **RANGE}).status_code == 422


def test_sin_accounts_422(make_client: ClientFactory) -> None:
    with make_client() as client:
        assert client.post("/jobs", json={"accounts": [], **RANGE}).status_code == 422


def test_list_jobs_lista_los_creados(make_client: ClientFactory, wait_done: WaitDone) -> None:
    with make_client([tweet_result("1")]) as client:
        a = client.post("/jobs", json={"accounts": ["ana"], **RANGE}).json()["id"]
        b = client.post("/jobs", json={"accounts": ["beto"], **RANGE}).json()["id"]
        wait_done(client, a)
        wait_done(client, b)
        listed = client.get("/jobs").json()
        ids = {j["id"] for j in listed}
        assert {a, b} <= ids
        assert len(listed) == 2


async def _boom_builder(settings: Settings) -> Backend:
    raise ProviderError("faltan cookies X_AUTH_TOKEN/X_CT0")


def test_job_con_backend_que_falla_queda_en_error(tmp_path: Path, wait_done: WaitDone) -> None:
    app = create_app(make_settings(tmp_path), backend_builder=_boom_builder)
    with TestClient(app) as client:
        job_id = client.post("/jobs", json={"accounts": ["someuser"], **RANGE}).json()["id"]
        done = wait_done(client, job_id)
        assert done["status"] == "error"
        assert "ProviderError" in done["error"]
        assert done["results"] is None
        # Un job en error no tiene CSV disponible.
        assert client.get(f"/jobs/{job_id}/csv/someuser").status_code == 409
