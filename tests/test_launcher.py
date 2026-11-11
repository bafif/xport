"""El lanzador `xport` (punto 14 de docs/PLAN.md): arranca el servicio si no corre, abre la
página y lo baja."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tweet_extractor import launcher
from tweet_extractor.config import Settings
from tweet_extractor.launcher import LaunchError, Probe


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _server(status: int, body: object) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (nombre de http.server)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def xport_port() -> Iterator[int]:
    yield from _server(200, {"status": "ok", "service": "xport", "pid": 4242})


@pytest.fixture
def other_port() -> Iterator[int]:
    yield from _server(200, {"status": "ok"})


@pytest.fixture
def missing_port() -> Iterator[int]:
    yield from _server(404, {"detail": "Not Found"})


# ── qué hay en un puerto ──


def test_reconoce_a_xport(xport_port: int) -> None:
    assert launcher.probe(xport_port) is Probe.XPORT
    assert launcher.health(xport_port) == (Probe.XPORT, 4242)


def test_otro_programa_en_el_puerto(other_port: int, missing_port: int) -> None:
    assert launcher.probe(other_port) is Probe.OTHER
    assert launcher.probe(missing_port) is Probe.OTHER


def test_puerto_libre() -> None:
    assert launcher.probe(_free_port()) is Probe.FREE


def test_el_puerto_sale_del_entorno() -> None:
    assert launcher.port_from_env({}) == 8000
    assert launcher.port_from_env({"XPORT_PORT": "8765"}) == 8765
    assert launcher.port_from_env({"XPORT_PORT": "no"}) == 8000


def test_el_servicio_corre_con_el_mismo_python(tmp_path: Path) -> None:
    cmd = launcher.service_command(8000, tmp_path / "python3")
    assert cmd[0] == str(tmp_path / "python3")
    assert cmd[1:5] == ["-I", "-m", "uvicorn", launcher.APP]  # aislado del entorno
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--port") + 1] == "8000"


# ── arrancar ──


class FakeProc:
    def __init__(self, code: int | None = None) -> None:
        self.code = code

    def poll(self) -> int | None:
        return self.code


def _probes(
    monkeypatch: pytest.MonkeyPatch, answers: dict[int, list[Probe]], pid: int | None = 4242
) -> None:
    """Cada puerto contesta su lista en orden; el último se repite. Un xport dice `pid`."""

    def fake(port: int) -> tuple[Probe, int | None]:
        seq = answers.get(port, [Probe.FREE])
        state = seq.pop(0) if len(seq) > 1 else seq[0]
        return state, pid if state is Probe.XPORT else None

    monkeypatch.setattr(launcher, "health", fake)


def _no_spawn(port: int, log: Path) -> FakeProc:
    raise AssertionError("no tenía que arrancar otro servicio")


def test_si_ya_corre_no_arranca_otro(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.XPORT]})
    url, started = launcher.ensure_running(Settings(), 8000, spawner=_no_spawn)
    assert (url, started) == ("http://localhost:8000/", False)


def test_si_corre_el_del_compose_usa_ese(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.FREE], 8080: [Probe.XPORT]})
    settings = Settings(xport_peer_ports=[8000, 8080])
    url, started = launcher.ensure_running(settings, 8000, spawner=_no_spawn)
    assert (url, started) == ("http://localhost:8080/", False)


def test_puerto_ocupado_por_otro_programa(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.OTHER]})
    with pytest.raises(LaunchError, match="8000"):
        launcher.ensure_running(Settings(), 8000, spawner=_no_spawn)


def test_arranca_y_espera_a_que_conteste(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.FREE, Probe.FREE, Probe.FREE, Probe.XPORT]})
    spawned: list[tuple[int, Path]] = []

    def spawner(port: int, log: Path) -> FakeProc:
        spawned.append((port, log))
        return FakeProc()

    settings = Settings()
    url, started = launcher.ensure_running(settings, 8000, spawner=spawner, sleep=lambda s: None)
    assert (url, started) == ("http://localhost:8000/", True)
    assert spawned == [(8000, settings.xport_data_dir / "service.log")]


def test_si_el_servicio_muere_dice_donde_esta_el_detalle(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.FREE]})
    settings = Settings()
    with pytest.raises(LaunchError, match="service.log"):
        launcher.ensure_running(
            settings, 8000, spawner=lambda p, log: FakeProc(1), sleep=lambda s: None
        )


def test_si_no_contesta_a_tiempo(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.FREE]})
    with pytest.raises(LaunchError, match="todavía no responde"):
        launcher.ensure_running(Settings(), 8000, spawner=lambda p, log: FakeProc(), timeout=0.0)


# ── bajar ──


def test_bajar_sin_servicio(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.FREE]})
    assert launcher.stop(8000, killer=lambda pid, force: None) is False


def test_bajar_un_xport_viejo_sin_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.XPORT]}, pid=None)
    with pytest.raises(LaunchError, match="versión anterior"):
        launcher.stop(8000, killer=lambda pid, force: None)


def test_el_xport_de_otro_usuario_no_se_toca(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.XPORT]})

    def denied(pid: int, force: bool) -> None:
        raise PermissionError(1, "Operation not permitted")

    with pytest.raises(LaunchError, match="otro usuario"):
        launcher.stop(8000, killer=denied)


def test_bajar_le_pide_que_cierre_y_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.XPORT, Probe.XPORT, Probe.FREE]})
    kills: list[tuple[int, bool]] = []
    lives = [True, False]
    stopped = launcher.stop(
        8000,
        killer=lambda pid, force: kills.append((pid, force)),
        alive=lambda pid: lives.pop(0) if len(lives) > 1 else lives[0],
        sleep=lambda s: None,
    )
    assert stopped is True
    assert kills == [(4242, False)]


def test_un_zombi_ya_bajo() -> None:
    """El servicio del native host queda sin esperar cuando termina: igual cuenta como bajado."""
    proc = launcher.subprocess.Popen(["true"])
    try:
        for _ in range(100):
            if not launcher.is_alive(proc.pid):
                break
            time.sleep(0.02)
        assert launcher.is_alive(proc.pid) is False  # todavía sin `wait`: es un zombi
    finally:
        proc.wait()


def test_si_no_cierra_lo_fuerza(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, {8000: [Probe.XPORT]})
    kills: list[tuple[int, bool]] = []
    with pytest.raises(LaunchError, match="4242"):
        launcher.stop(
            8000,
            killer=lambda pid, force: kills.append((pid, force)),
            alive=lambda pid: True,
            timeout=0.0,
            sleep=lambda s: None,
        )
    assert kills == [(4242, False), (4242, True)]


# ── la línea de comandos ──


def test_abre_la_pagina(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        launcher, "ensure_running", lambda settings, port: ("http://localhost:8000/", True)
    )
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    assert launcher.main([]) == 0
    assert opened == ["http://localhost:8000/"]
    assert "xport arrancó" in capsys.readouterr().out


def test_start_no_abre_la_pagina(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        launcher, "ensure_running", lambda settings, port: ("http://localhost:8000/", False)
    )
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: pytest.fail("abrió la página"))
    assert launcher.main(["start"]) == 0


def test_en_ingles(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _probes(monkeypatch, {8000: [Probe.FREE]})
    assert launcher.main(["status", "--lang", "en"]) == 3
    assert capsys.readouterr().out.strip() == "xport is not running."


def test_un_error_se_notifica_sin_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(settings: Settings, port: int) -> tuple[str, bool]:
        raise LaunchError("ocupado")

    sent: list[tuple[str, str]] = []

    async def notify(title: str, body: str, open_path: Path | None = None) -> None:
        sent.append((title, body))

    monkeypatch.setattr(launcher, "ensure_running", busy)
    monkeypatch.setattr(launcher, "system_notify", notify)
    monkeypatch.setenv("XPORT_NOTIFY", "true")
    assert launcher.main(["start"]) == 1
    assert sent == [("xport", "ocupado")]  # pytest no le da una terminal a stderr


# ── de punta a punta, con el servicio de verdad ──


def test_arranca_y_baja_el_servicio_de_verdad(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XPORT_BROWSER", "false")
    settings = Settings()
    port = _free_port()
    procs: list[launcher.subprocess.Popen[bytes]] = []

    def spawner(port: int, log: Path) -> launcher.subprocess.Popen[bytes]:
        proc = launcher.spawn(port, log)
        procs.append(proc)
        # Hijo de los tests: alguien tiene que esperarlo para que no quede como zombi.
        threading.Thread(target=proc.wait, daemon=True).start()
        return proc

    try:
        url, started = launcher.ensure_running(settings, port, spawner=spawner)
        assert (url, started) == (f"http://localhost:{port}/", True)
        assert launcher.health(port) == (Probe.XPORT, procs[0].pid)
        assert launcher.stop(port) is True
        assert launcher.probe(port) is Probe.FREE
        # Cerró ordenado (uvicorn vuelve a levantar la señal al terminar: sale con -15).
        assert procs[0].wait(timeout=5) in (0, -15)
        log = (settings.xport_data_dir / "service.log").read_text(encoding="utf-8")
        assert "Application shutdown complete" in log
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()


# ── lo que hace falta para capturar sin ventana (`xport check`) ──


def test_como_instalar_cage_segun_la_distro(tmp_path: Path) -> None:
    rel = tmp_path / "os-release"
    assert launcher.package_command("cage", rel) is None  # sin el archivo
    rel.write_text("ID=arch\n")
    assert launcher.package_command("cage", rel) == "sudo pacman -S cage"
    rel.write_text('NAME="Linux Mint"\nID=linuxmint\nID_LIKE="ubuntu debian"\n')
    assert launcher.package_command("cage", rel) == "sudo apt install cage"
    rel.write_text("ID=fedora\n")
    assert launcher.package_command("cage", rel) == "sudo dnf install cage"
    rel.write_text("ID=nixos\n")
    assert launcher.package_command("cage", rel) is None


def test_lo_que_falta_para_capturar_sin_ventana(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "find_browser", lambda explicit: None)
    monkeypatch.setattr(launcher, "find_virtual_display", lambda explicit=None: None)
    monkeypatch.setattr(launcher, "package_command", lambda package: f"sudo pacman -S {package}")
    settings = Settings(xport_extension_dir=tmp_path)
    lines = launcher.requirements(settings, "linux")
    assert [ok for ok, _ in lines] == [False, False, False]
    assert "sudo pacman -S cage" in lines[1][1]
    windows = launcher.requirements(settings, "win32")
    assert "todavía no captura sin ventana" in windows[1][1]


def test_check_con_todo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "manifest.json").write_text("{}")
    monkeypatch.setattr(launcher, "find_browser", lambda explicit: Path("/usr/bin/chromium"))
    monkeypatch.setattr(
        launcher, "find_virtual_display", lambda explicit=None: Path("/usr/bin/cage")
    )
    monkeypatch.setenv("XPORT_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    assert launcher.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "✔ Navegador: /usr/bin/chromium" in out
    assert "✔ Pantalla invisible (cage): /usr/bin/cage" in out
