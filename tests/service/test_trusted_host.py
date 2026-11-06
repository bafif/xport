from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.service.conftest import make_settings
from tweet_extractor.service.app import create_app


def test_acepta_localhost_y_rechaza_otro_host(tmp_path: Path) -> None:
    # Con los hosts por defecto: la extensión y el native host hablan con localhost o
    # 127.0.0.1; una página con DNS rebinding llega con su propio dominio y recibe 400.
    settings = make_settings(tmp_path, allowed_hosts=["localhost", "127.0.0.1"])
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz", headers={"host": "127.0.0.1:8000"}).status_code == 200
        assert client.get("/healthz", headers={"host": "malicioso.example"}).status_code == 400
        assert (
            client.post(
                "/reset", json={"account": "nasa"}, headers={"host": "malicioso.example"}
            ).status_code
            == 400
        )
