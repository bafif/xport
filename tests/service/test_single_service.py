from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.service.conftest import make_settings
from tweet_extractor.service.app import AnotherServiceError, create_app


@pytest.fixture
def fake_service() -> Iterator[Callable[[bytes], int]]:
    """Levanta un servidor HTTP que responde `/healthz` con `body`; devuelve su puerto."""
    servers: list[ThreadingHTTPServer] = []

    def _start(body: bytes) -> int:
        class Health(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200 if self.path == "/healthz" else 404)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return int(server.server_address[1])

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_no_arranca_si_ya_corre_otro_xport(
    tmp_path: Path, fake_service: Callable[[bytes], int]
) -> None:
    # Regla 1 de docs/PLAN.md: dos servicios serían dos registros del tope.
    port = fake_service(b'{"status": "ok", "service": "xport"}')
    app = create_app(make_settings(tmp_path, xport_peer_ports=[port]))
    with pytest.raises(
        AnotherServiceError, match=f"otro xport corriendo en http://localhost:{port}"
    ):
        with TestClient(app):
            pass
    assert not (tmp_path / "audit" / "ledger.db").exists()  # ni llegó a abrir el registro


def test_arranca_si_en_el_puerto_hay_otra_cosa(
    tmp_path: Path, fake_service: Callable[[bytes], int]
) -> None:
    other = fake_service(b'{"status": "ok"}')
    not_json = fake_service(b"<html></html>")
    app = create_app(make_settings(tmp_path, xport_peer_ports=[other, not_json, 9]))
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok", "service": "xport"}
