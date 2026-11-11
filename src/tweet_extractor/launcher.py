"""`xport`, lo que corre el acceso directo del instalador (punto 14 de docs/PLAN.md).

- `xport` o `xport open`: arranca el servicio si no está corriendo y abre la página local en el
  navegador de siempre.
- `xport start`: lo arranca sin abrir la página.
- `xport stop`: lo baja (el instalador lo usa antes de reemplazar los archivos), con el número
  de proceso que el servicio dice en `/healthz`.
- `xport status`: dice si está corriendo.
- `xport check`: dice si están el navegador, la pantalla virtual y la extensión, y cómo
  instalar lo que falte (el instalador lo muestra al terminar).

El servicio queda en segundo plano, separado del lanzador: sigue vivo cuando el lanzador
termina, porque lo usan la página, la extensión y el navegador propio de xport. Si ya corre otro
xport (el del native host, el del compose o uno a mano), no arranca un segundo: dos servicios
llevarían dos registros del tope (regla 1 de docs/PLAN.md).

Desde un acceso directo no hay terminal: si algo sale mal, además de escribirlo, lo muestra como
notificación del sistema.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from tweet_extractor.config import Settings
from tweet_extractor.i18n import LANGS, argv_lang, parse_lang, set_lang, system_lang, t
from tweet_extractor.service.browser import find_browser, find_virtual_display
from tweet_extractor.service.notify import system_notify
from tweet_extractor.service.routers import SERVICE_NAME

APP = "tweet_extractor.service.app:app"
# El puerto que usan la extensión (`extension/lib/api.ts`) y el native host.
DEFAULT_PORT = 8000
READY_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 15.0
# En Windows, una conexión a un puerto cerrado de la propia máquina tarda unos dos segundos en
# ser rechazada (reintenta antes de rendirse).
CONNECT_TIMEOUT_S = 5.0
HEALTH_TIMEOUT_S = 3.0
POLL_S = 0.25
LOG_FILE = "service.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
COMMANDS = ("open", "start", "stop", "status", "check")
OS_RELEASE = Path("/etc/os-release")


class Probe(StrEnum):
    XPORT = "xport"  # contesta un xport
    OTHER = "other"  # el puerto lo usa otro programa, o algo escucha y no contesta
    FREE = "free"  # nadie escucha


class LaunchError(RuntimeError):
    """No se pudo arrancar o bajar el servicio. El mensaje ya está en el idioma de la persona."""


class Process(Protocol):
    def poll(self) -> int | None: ...


Spawner = Callable[[int, Path], Process]
Killer = Callable[[int, bool], None]
Alive = Callable[[int], bool]
Sleep = Callable[[float], None]


def port_from_env(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get("XPORT_PORT", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_PORT


def page_url(port: int) -> str:
    return f"http://localhost:{port}/"


def log_path(settings: Settings) -> Path:
    return settings.xport_data_dir / LOG_FILE


def health(port: int) -> tuple[Probe, int | None]:
    """Qué hay en `port` de esta computadora y, si es un xport, el número de su proceso, que
    dice `/healthz` (uno anterior al 11/11/2026 no lo dice). Primero prueba solo la conexión:
    así un puerto cerrado de Windows, que tarda en rechazarla, no se confunde con algo que
    escucha y no contesta. Un servicio que todavía está arrancando no abre el puerto hasta
    estar listo (uvicorn): `FREE`. Sin proxies: es la propia máquina."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT_S):
            pass
    except ConnectionRefusedError:
        return Probe.FREE, None
    except OSError:
        return Probe.OTHER, None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=HEALTH_TIMEOUT_S) as r:
            body = json.loads(r.read(4096))
    except (OSError, ValueError):  # URLError y HTTPError también son OSError
        return Probe.OTHER, None
    if not (isinstance(body, dict) and body.get("service") == SERVICE_NAME):
        return Probe.OTHER, None
    pid = body.get("pid")
    return Probe.XPORT, pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def probe(port: int) -> Probe:
    return health(port)[0]


def service_command(port: int, python: Path | None = None) -> list[str]:
    """Cómo se arranca el servicio: con el mismo Python que corre el lanzador (el del
    instalador, o el del venv), solo en la propia computadora y sin anotar cada pedido (la
    página consulta la cola cada pocos segundos y llenaría el log)."""
    exe = Path(sys.executable) if python is None else python
    if sys.platform == "win32":
        # pythonw.exe no abre una consola: el servicio corre sin ninguna ventana.
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            exe = quiet
    # `-I`: ni PYTHONPATH ni los paquetes del usuario se meten en el Python del instalador.
    return [
        str(exe),
        "-I",
        "-m",
        "uvicorn",
        APP,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
    ]


def _rotate(log: Path) -> None:
    """Un solo log viejo: el de antes de pasar los 5 MB."""
    with contextlib.suppress(OSError):
        if log.stat().st_size > LOG_MAX_BYTES:
            os.replace(log, log.with_name(log.name + ".1"))


def spawn(port: int, log: Path) -> subprocess.Popen[bytes]:
    """Arranca el servicio separado del lanzador: en su propia sesión (Linux, macOS) o sin
    consola y en su propio grupo (Windows), así no muere cuando el lanzador termina. Lo que
    escribe va a `log`. Corre en la carpeta de datos, para no depender de desde dónde se
    abrió el acceso directo."""
    log.parent.mkdir(parents=True, exist_ok=True)
    _rotate(log)
    cmd = service_command(port)
    with log.open("ab") as out:
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            return subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                cwd=log.parent,
                creationflags=flags,
            )
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            cwd=log.parent,
            start_new_session=True,
        )


def ensure_running(
    settings: Settings,
    port: int,
    *,
    spawner: Spawner = spawn,
    timeout: float = READY_TIMEOUT_S,
    sleep: Sleep = time.sleep,
) -> tuple[str, bool]:
    """La página del xport que corre, arrancándolo si hace falta. Devuelve la dirección y si
    lo arrancó recién."""
    state = probe(port)
    if state is Probe.XPORT:
        return page_url(port), False
    for peer in settings.xport_peer_ports:
        if peer != port and probe(peer) is Probe.XPORT:
            return page_url(peer), False
    if state is Probe.OTHER:
        raise LaunchError(t("launcher.port_busy", port=port))
    log = log_path(settings)
    proc = spawner(port, log)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe(port) is Probe.XPORT:
            return page_url(port), True
        if proc.poll() is not None:
            raise LaunchError(t("launcher.died", log=log))
        sleep(POLL_S)
    raise LaunchError(t("launcher.slow", log=log))


def terminate(pid: int, force: bool = False) -> None:
    """Le pide al servicio que baje. En Linux y macOS, SIGTERM: uvicorn cierra ordenado y el
    servicio cierra su navegador. En Windows no hay a quién mandarle una señal (corre sin
    consola), así que va `taskkill` con todo lo que abrió; la base del tope no se rompe
    porque cada acceso se anota en una transacción."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def _zombie(pid: int) -> bool:
    """Si el proceso ya terminó y nadie lo esperó todavía: para `stop`, ya bajó. Pasa con el
    servicio que levantó el native host, que no espera a su uvicorn hasta que el navegador
    cierra el canal."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return stat.rpartition(")")[2].split()[:1] == ["Z"]
    except OSError:
        pass
    try:  # macOS: sin /proc
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return False
    return out.strip().startswith("Z")


def is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _zombie(pid)


def stop(
    port: int,
    *,
    killer: Killer = terminate,
    alive: Alive = is_alive,
    timeout: float = STOP_TIMEOUT_S,
    sleep: Sleep = time.sleep,
) -> bool:
    """Baja el xport que contesta en `port` y espera a que su proceso termine (el instalador
    reemplaza los archivos después). El número de proceso lo da el propio servicio en
    `/healthz`: es el que atiende el puerto ahora, y no uno anotado antes que el sistema pudo
    haberle dado a otro programa. Devuelve `False` si no había ninguno."""
    state, pid = health(port)
    if state is not Probe.XPORT:
        return False
    if pid is None:
        raise LaunchError(t("launcher.no_pid", url=page_url(port)))
    for force in (False, True):
        try:
            killer(pid, force)
        except PermissionError as e:
            raise LaunchError(t("launcher.not_yours", pid=pid)) from e
        deadline = time.monotonic() + (timeout if not force else 5.0)
        while time.monotonic() < deadline:
            if not alive(pid) and probe(port) is not Probe.XPORT:
                return True
            sleep(POLL_S)
    raise LaunchError(t("launcher.stop_failed", pid=pid))


def package_command(package: str, os_release: Path = OS_RELEASE) -> str | None:
    """Cómo se instala un paquete en esta distribución de Linux, si es una conocida."""
    ids: list[str] = []
    try:
        for line in os_release.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key in ("ID", "ID_LIKE"):
                ids += value.strip().strip('"').lower().split()
    except OSError:
        return None
    for family, tool in (
        ("arch", "pacman -S"),
        ("alpine", "apk add"),
        ("debian", "apt install"),
        ("ubuntu", "apt install"),
        ("fedora", "dnf install"),
        ("rhel", "dnf install"),
        ("suse", "zypper install"),
        ("opensuse", "zypper install"),
    ):
        if family in ids:
            return f"sudo {tool} {package}"
    return None


def requirements(settings: Settings, platform: str | None = None) -> list[tuple[bool, str]]:
    """Lo que hace falta para capturar sin ninguna ventana (punto 13 de docs/PLAN.md): cada
    cosa, si está, y qué hacer si falta."""
    browser = find_browser(settings.xport_browser_bin)
    ext = settings.xport_extension_dir
    has_ext = ext is not None and (ext / "manifest.json").is_file()
    lines = [
        (True, t("check.browser", path=browser))
        if browser is not None
        else (False, t("check.no_browser")),
        (True, t("check.extension", path=ext)) if has_ext else (False, t("check.no_extension")),
    ]
    if (platform or sys.platform).startswith("linux"):
        cage = find_virtual_display(settings.xport_browser_cage)
        if cage is not None:
            lines.insert(1, (True, t("check.cage", path=cage)))
        else:
            cmd = package_command("cage")
            hint = t("check.no_cage_cmd", cmd=cmd) if cmd else t("check.no_cage")
            lines.insert(1, (False, hint))
    else:
        # Windows y macOS todavía no tienen el navegador sin ventana (el plan B del punto 13).
        lines.insert(1, (False, t("check.no_hidden_browser", path=ext or "-")))
    return lines


def print_requirements(settings: Settings) -> bool:
    """Las muestra y dice si está todo."""
    lines = requirements(settings)
    for ok, line in lines:
        print(f"{'✔' if ok else '✘'} {line}")
    print(t("check.data_dir", path=settings.xport_data_dir))
    return all(ok for ok, _ in lines)


def open_page(url: str) -> None:
    if not webbrowser.open(url):
        print(t("launcher.open_manually", url=url))


def report(message: str, *, notify: bool) -> None:
    """El error en la terminal y, si no hay terminal (un acceso directo), en una notificación."""
    print(message, file=sys.stderr)
    if notify and not (sys.stderr is not None and sys.stderr.isatty()):
        asyncio.run(system_notify(t("launcher.failed_title"), message))


def safe_console() -> None:
    """Que un carácter que la salida no tiene no corte el programa: la consola de Windows
    redirigida a un archivo escribe en su página de códigos vieja, que no tiene ✔."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xport", description=t("launcher.help"))
    parser.add_argument(
        "command", nargs="?", default="open", choices=COMMANDS, help=t("launcher.opt_command")
    )
    parser.add_argument("--lang", choices=LANGS, help=t("cli.opt_lang"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    safe_console()
    args_list = list(sys.argv[1:] if argv is None else argv)
    set_lang(parse_lang(argv_lang(args_list)) or system_lang())
    args = build_parser().parse_args(args_list)
    try:
        settings = Settings()
    except ValidationError as e:
        report(str(e), notify=True)
        return 1
    port = port_from_env()
    try:
        if args.command == "status":
            running = probe(port) is Probe.XPORT
            print(
                t("launcher.status_running", url=page_url(port))
                if running
                else t("launcher.not_running")
            )
            return 0 if running else 3
        if args.command == "check":
            return 0 if print_requirements(settings) else 2
        if args.command == "stop":
            print(t("launcher.stopped") if stop(port) else t("launcher.not_running"))
            return 0
        url, started = ensure_running(settings, port)
    except LaunchError as e:
        report(str(e), notify=settings.xport_notify)
        return 1
    print(t("launcher.started" if started else "launcher.running", url=url))
    if args.command == "open":
        open_page(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
