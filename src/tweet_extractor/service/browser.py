"""El Chrome sin ventana que maneja el servicio (punto 13 de docs/PLAN.md).

Mientras haya capturas sin terminar en la cola, el servicio levanta un Chrome (o Chromium,
o Edge) con un perfil propio de xport y sin ventana, y le carga la extensión por el canal de
depuración (`--remote-debugging-pipe`). Es la misma extensión de siempre: toma el trabajo
de la cola, abre la búsqueda, la scrollea y manda a `/ingest` lo que x.com descarga, con el
tope de siempre (regla 1). Cuando la cola se vacía, el navegador se cierra.

Solo ese navegador recibe capturas: el servicio le deja una marca en el storage de la
extensión (`xport:runner`) y `POST /captures/next` la pide. La extensión instalada en el
navegador de todos los días deja de abrir pestañas mientras el de xport ande; si el de xport
no puede arrancar (no hay Chrome, o falló varias veces seguidas), la cola vuelve a estar
abierta para cualquier extensión, como antes.

La sesión de X del perfil: si no hay, el servicio la inicia con la cookie del `.env`
(`X_AUTH_TOKEN`, de una cuenta aparte) cuando está configurada. Si no hay cookie o X la
rechaza, abre el mismo perfil en una ventana chica en el inicio de sesión de X y avisa con
una notificación; cuando la sesión aparece, cierra la ventana y sigue sin ventana.

El canal de depuración se usa solo para eso: cargar la extensión, dejarle la marca y mirar
si hay sesión. No maneja la página: no es Selenium ni Playwright (regla 5 de docs/PLAN.md),
y el navegador corre en la computadora, nunca en la imagen del servicio.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from tweet_extractor.config import Settings
from tweet_extractor.i18n import Note
from tweet_extractor.i18n import t as text
from tweet_extractor.storage.queue import CaptureQueue

log = logging.getLogger(__name__)

# Los navegadores que sirven, en orden de preferencia. Todos aceptan cargar una extensión
# por el pipe de depuración (Chrome desde la 137 ignora `--load-extension`).
BROWSER_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "google-chrome-beta",
    "microsoft-edge-beta",
)
STORAGE_RUNNER = "xport:runner"  # la marca que deja el servicio (extension/lib/capture.ts)
LOGIN_URL = "https://x.com/i/flow/login"
SESSION_COOKIE = "auth_token"


def find_browser(explicit: Path | None) -> Path | None:
    """El ejecutable del navegador: el configurado (`XPORT_BROWSER_BIN`) o el primero de
    `BROWSER_NAMES` que esté en el PATH."""
    if explicit is not None:
        return explicit if explicit.is_file() else None
    for name in BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def find_virtual_display(explicit: Path | None = None) -> Path | None:
    """`cage`, el compositor de Wayland que corre sin pantalla real: adentro, Chrome tiene
    ventana (y X lo ve como un Chrome común) pero nadie la ve. Solo en Linux."""
    if not sys.platform.startswith("linux"):
        return None
    if explicit is not None:
        return explicit if explicit.is_file() else None
    found = shutil.which("cage")
    return Path(found) if found else None


def virtual_display_env(base: Mapping[str, str]) -> dict[str, str]:
    """El entorno para `cage` sin pantalla: sin las pantallas de la persona, con el backend
    sin salida de wlroots y dibujo por software (no pide placa de video)."""
    env = {k: v for k, v in base.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update(WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1", WLR_RENDERER="pixman")
    return env


def browser_args(profile: Path, *, headless: bool, url: str, virtual: bool = False) -> list[str]:
    """Los argumentos de Chrome. El pipe de depuración usa los descriptores 3 (lo que le
    mandamos) y 4 (lo que contesta); `--enable-unsafe-extension-debugging` habilita cargar
    la extensión por ahí. Oculto, va adentro de una pantalla virtual (`virtual`) o, si no
    hay, con `--headless`. X le contesta 403 a todo lo que se presenta como HeadlessChrome
    (medido el 10 de noviembre de 2026, a la tarde), así que `--headless` queda de último
    recurso y no se disfraza."""
    args = [
        f"--user-data-dir={profile}",
        "--remote-debugging-pipe",
        "--enable-unsafe-extension-debugging",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
    ]
    if headless and virtual:
        args += ["--ozone-platform=wayland", "--window-size=1280,1600"]
    elif headless:
        args += ["--headless", "--window-size=1280,1600"]
    else:
        args += ["--window-size=520,760", f"--app={url}"]
        return args
    return [*args, url]


# ── El canal de depuración ───────────────────────────────────────────────────────────


class CdpError(RuntimeError):
    """El navegador contestó con un error, no contestó o se cerró."""


def encode_message(msg_id: int, method: str, params: dict[str, Any], session: str | None) -> bytes:
    """Un mensaje del pipe: JSON terminado en un byte nulo."""
    msg: dict[str, Any] = {"id": msg_id, "method": method, "params": params}
    if session is not None:
        msg["sessionId"] = session
    return json.dumps(msg).encode() + b"\0"


class Cdp(Protocol):
    async def call(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]: ...


class PipeCdp:
    """El protocolo de depuración de Chrome por el pipe: un pedido por vez (los eventos que
    llegan en el medio se descartan)."""

    def __init__(self, reader: asyncio.StreamReader, write_fd: int) -> None:
        self._reader = reader
        self._write_fd = write_fd
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def call(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            try:
                os.write(self._write_fd, encode_message(msg_id, method, params or {}, session))
                while True:
                    raw = await asyncio.wait_for(self._reader.readuntil(b"\0"), timeout=30)
                    reply = json.loads(raw[:-1])
                    if reply.get("id") == msg_id:
                        break
            except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
                raise CdpError(f"{method}: el navegador no contestó ({exc!r})") from exc
        if "error" in reply:
            raise CdpError(f"{method}: {reply['error'].get('message', reply['error'])}")
        result: dict[str, Any] = reply.get("result", {})
        return result

    def close(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self._write_fd)


@dataclass
class Browser:
    """Un navegador lanzado: su proceso y su canal de depuración."""

    process: asyncio.subprocess.Process
    cdp: Cdp
    headless: bool

    @property
    def alive(self) -> bool:
        return self.process.returncode is None

    async def close(self) -> None:
        if self.alive:
            with contextlib.suppress(CdpError):
                await self.cdp.call("Browser.close")
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if isinstance(self.cdp, PipeCdp):
            self.cdp.close()


Launcher = Callable[[Path, Path, bool, str], Awaitable[Browser]]


async def launch(
    binary: Path, profile: Path, headless: bool, url: str, cage: Path | None = None
) -> Browser:
    """Lanza el navegador con el pipe de depuración en los descriptores 3 y 4. Los mueve
    un `sh` antes de pasarle el control al navegador (`exec`), porque Python pasa los
    descriptores con su mismo número. Oculto y con `cage`, el navegador corre adentro de
    la pantalla virtual, que le pasa los descriptores."""
    to_browser_r, to_browser_w = os.pipe()
    from_browser_r, from_browser_w = os.pipe()
    virtual = headless and cage is not None
    prefix = f'"{cage}" -- ' if virtual else ""
    script = (
        f'exec {prefix}"$0" "$@" 3<&{to_browser_r} 4>&{from_browser_w} '
        f"{to_browser_r}<&- {from_browser_w}>&-"
    )
    env = virtual_display_env(os.environ) if virtual else None
    try:
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            script,
            str(binary),
            *browser_args(profile, headless=headless, url=url, virtual=virtual),
            env=env,
            pass_fds=(to_browser_r, from_browser_w),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        for fd in (to_browser_r, to_browser_w, from_browser_r, from_browser_w):
            os.close(fd)
        raise
    os.close(to_browser_r)
    os.close(from_browser_w)
    reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(from_browser_r, "rb")
    )
    return Browser(process=process, cdp=PipeCdp(reader, to_browser_w), headless=headless)


class LoginWindow(Protocol):
    """El navegador abierto para iniciar sesión (un `asyncio.subprocess.Process`)."""

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


LoginLauncher = Callable[[Path, str], Awaitable[LoginWindow]]
SessionProbe = Callable[[Path], bool]


async def launch_login_window(binary: Path, profile: Path, url: str) -> LoginWindow:
    """Abre el perfil de xport en una ventana común de Chrome, con pestañas y ventanas
    emergentes, y sin canal de depuración ni ninguna opción especial: para X es un Chrome
    como cualquier otro. Una ventana chica en modo aplicación y con el pipe enganchado no
    dejaba terminar el inicio de sesión con una cuenta real (10/11/2026)."""
    return await asyncio.create_subprocess_exec(
        str(binary),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )


def profile_has_session(profile: Path) -> bool:
    """Si la base de cookies del perfil tiene la de sesión de X. Mira solo que exista, no su
    valor. Lee una copia, dentro del mismo perfil (700), para no chocar con el navegador
    que la está usando. Chrome la escribe cada tanto y al cerrarse."""
    db = profile / "Default" / "Cookies"
    if not db.is_file():
        return False
    copy = profile / ".xport-sesion"
    try:
        shutil.copyfile(db, copy)
        with contextlib.closing(sqlite3.connect(f"file:{copy}?mode=ro", uri=True)) as con:
            row = con.execute(
                "SELECT 1 FROM cookies WHERE name = ? AND (host_key = 'x.com' "
                "OR host_key LIKE '%.x.com') LIMIT 1",
                (SESSION_COOKIE,),
            ).fetchone()
        return row is not None
    except (OSError, sqlite3.Error):
        return False
    finally:
        with contextlib.suppress(OSError):
            copy.unlink()


async def close_login_window(window: LoginWindow) -> None:
    """La cierra como al cerrar la ventana (Chrome guarda las cookies al salir)."""
    if window.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            window.terminate()
        try:
            await asyncio.wait_for(window.wait(), timeout=15)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                window.kill()
            await window.wait()


# ── Lo que hace el servicio con el navegador ─────────────────────────────────────────


class RunnerState(StrEnum):
    OFF = "off"  # apagado por configuración, o no hay navegador / extensión
    IDLE = "idle"  # nada en la cola: el navegador está cerrado
    RUNNING = "running"  # capturando sin ventana
    LOGIN = "login"  # esperando que la persona inicie sesión en X en la ventana
    FAILED = "failed"  # no pudo: la cola queda abierta para cualquier extensión


@dataclass(frozen=True)
class RunnerStatus:
    state: RunnerState
    note: Note | None = None  # en el idioma de quien lo lee: ver `tweet_extractor.i18n`


class Notify(Protocol):
    def post(self, title: str, body: str, path: Path | None = None) -> None: ...


class BrowserRunner:
    """Levanta y baja el navegador de xport según la cola (ver el docstring del módulo)."""

    POLL_S = 5.0
    IDLE_STOP_S = 60.0  # cuánto sigue abierto con la cola vacía
    SESSION_CHECK_S = 60.0
    LOGIN_TIMEOUT_S = 30 * 60.0
    LOGIN_POLL_S = 3.0
    MAX_CRASHES = 3  # caídas seguidas antes de rendirse

    def __init__(
        self,
        settings: Settings,
        queue: CaptureQueue,
        notifier: Notify,
        *,
        launcher: Launcher | None = None,
        login_launcher: LoginLauncher | None = None,
        session_probe: SessionProbe = profile_has_session,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._notifier = notifier
        self.token = secrets.token_urlsafe(24)
        self._binary = find_browser(settings.xport_browser_bin) if settings.xport_browser else None
        self._cage = find_virtual_display(settings.xport_browser_cage)
        ext = settings.xport_extension_dir
        self._extension = ext if ext is not None and (ext / "manifest.json").is_file() else None
        self._launcher = launcher or self._launch
        self._login_launcher = login_launcher or self._launch_login
        self._session_probe = session_probe
        self._login_window: LoginWindow | None = None
        self._browser: Browser | None = None
        self._task: asyncio.Task[None] | None = None
        self._idle_since: float | None = None
        self._last_session_check = 0.0
        self._crashes = 0
        self._env_cookie_tried = False
        self._error_seen = False
        self._status = RunnerStatus(RunnerState.IDLE)
        if not settings.xport_browser:
            self._status = RunnerStatus(RunnerState.OFF, Note(key="runner.off_config"))
        elif self._cage is None:
            # Sin pantalla virtual quedaría `--headless`, y X le contesta 403: la captura no
            # avanzaría nunca. Mejor que la tome la extensión del navegador de siempre.
            self._status = RunnerStatus(RunnerState.OFF, Note(key="runner.no_cage"))
        elif self._binary is None:
            self._status = RunnerStatus(RunnerState.OFF, Note(key="runner.no_browser"))
        elif self._extension is None:
            self._status = RunnerStatus(RunnerState.OFF, Note(key="runner.no_extension"))

    async def _launch_login(self, profile: Path, url: str) -> LoginWindow:
        assert self._binary is not None
        return await launch_login_window(self._binary, profile, url)

    async def _launch(self, profile: Path, extension: Path, headless: bool, url: str) -> Browser:
        assert self._binary is not None
        return await launch(self._binary, profile, headless, url, cage=self._cage)

    # ── lo que ven los demás ──

    @property
    def status(self) -> RunnerStatus:
        return self._status

    @property
    def active(self) -> bool:
        """Si el navegador de xport es el que toma la cola."""
        return self._status.state not in (RunnerState.OFF, RunnerState.FAILED)

    def accepts(self, token: str | None) -> bool:
        """Si a quien pide trabajo con esta marca le toca la cola. Con el navegador de xport
        en marcha, solo a él; si no anda, a cualquiera (la extensión de siempre)."""
        return not self.active or (token is not None and secrets.compare_digest(token, self.token))

    def wake(self) -> None:
        """Alguien encoló: si se había rendido, vuelve a intentar."""
        if self._status.state == RunnerState.FAILED:
            self._crashes = 0
            self._env_cookie_tried = False
            self._status = RunnerStatus(RunnerState.IDLE)

    # ── ciclo de vida ──

    def start(self) -> None:
        if self._status.state == RunnerState.OFF or self._task is not None:
            return
        self._task = asyncio.create_task(self._supervise(), name="xport-browser")

    async def aclose(self) -> None:
        if self._login_window is not None:
            await close_login_window(self._login_window)
            self._login_window = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_browser()

    async def _supervise(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — el supervisor no se cae por un error suelto
                log.exception("navegador de xport: error en la vuelta del supervisor")
            await asyncio.sleep(self.POLL_S)

    async def tick(self, now: float | None = None) -> None:
        """Una vuelta: arranca, cuida o cierra el navegador según la cola."""
        now = time.monotonic() if now is None else now
        if self._status.state in (RunnerState.OFF, RunnerState.FAILED, RunnerState.LOGIN):
            return
        pending = await self._queue.has_unfinished()
        browser = self._browser
        if browser is not None and not browser.alive:
            self._browser = None
            browser = None
            if pending:
                self._crash(Note(key="runner.closed"))
                if self._status.state == RunnerState.FAILED:
                    return
        if not pending:
            if browser is None:
                self._status = RunnerStatus(RunnerState.IDLE)
                return
            if self._idle_since is None:
                self._idle_since = now
            if now - self._idle_since >= self.IDLE_STOP_S:
                await self._close_browser()
                self._status = RunnerStatus(RunnerState.IDLE)
            return
        self._idle_since = None
        if browser is None:
            await self._start_headless()
            self._last_session_check = now
            return
        if now - self._last_session_check >= self.SESSION_CHECK_S:
            self._last_session_check = now
            if await self._stuck_on_error(browser):
                await self._close_browser()
                self._crash(Note(key="runner.error_page"))
                return
            if not await self._ensure_session(browser):
                await self._login()

    async def _stuck_on_error(self, browser: Browser) -> bool:
        """Si una pestaña quedó en una página de error de Chrome (X contestó 403, la red se
        cayó) en dos chequeos seguidos: el navegador no avanza solo."""
        targets = (await browser.cdp.call("Target.getTargets")).get("targetInfos", [])
        on_error = any(
            t.get("type") == "page" and str(t.get("url", "")).startswith("chrome-error://")
            for t in targets
        )
        stuck = on_error and self._error_seen
        self._error_seen = on_error
        return stuck

    def _crash(self, note: Note) -> None:
        self._crashes += 1
        if self._crashes >= self.MAX_CRASHES:
            failed = Note(key="runner.crashed", params={"reason": note, "count": self._crashes})
            log.error("navegador de xport: %s", failed.render())
            self._status = RunnerStatus(RunnerState.FAILED, failed)
            self._notifier.post(text("notify.runner_failed"), failed.render())
        else:
            log.warning("navegador de xport: %s (vez %d)", note.render(), self._crashes)

    async def _open(self, headless: bool, url: str) -> Browser:
        profile = self._settings.browser_profile_dir
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        profile.chmod(0o700)  # guarda la sesión de X: solo para esta cuenta
        # Chrome guarda en el perfil el service worker de la extensión y, con la misma
        # versión en el manifiesto, lo sigue usando aunque `dist/` tenga otro armado: el
        # 10/11/2026 corrió el background de antes de la Fase 4 y la captura no avanzó. Sin
        # esa caché, cada arranque usa la extensión que está en disco.
        shutil.rmtree(profile / "Default" / "Service Worker", ignore_errors=True)
        assert self._extension is not None
        return await self._launcher(profile, self._extension, headless, url)

    async def _start_headless(self) -> None:
        try:
            browser = await self._open(headless=True, url="about:blank")
        except OSError as exc:
            self._crash(Note(key="runner.launch_failed", params={"error": str(exc)}))
            return
        self._browser = browser
        try:
            ext_id = await self._load_extension(browser)
            if not await self._ensure_session(browser):
                await self._login()
                return
            await self._mark(browser, ext_id)
        except CdpError as exc:
            await self._close_browser()
            self._crash(Note(key="runner.extension_refused", params={"error": str(exc)}))
            return
        self._crashes = 0
        self._status = RunnerStatus(RunnerState.RUNNING)
        log.info("navegador de xport: capturando sin ventana")

    async def _load_extension(self, browser: Browser) -> str:
        assert self._extension is not None
        result = await browser.cdp.call("Extensions.loadUnpacked", {"path": str(self._extension)})
        ext_id = result.get("id")
        if not isinstance(ext_id, str):
            raise CdpError("Extensions.loadUnpacked no devolvió el id")
        return ext_id

    async def _mark(self, browser: Browser, ext_id: str) -> None:
        """Le deja la marca a la extensión. El storage de una extensión se escribe desde una
        sesión enganchada a su service worker, que tarda un momento en aparecer."""
        session = await self._attach_worker(browser, ext_id)
        await browser.cdp.call(
            "Extensions.setStorageItems",
            {"id": ext_id, "storageArea": "local", "values": {STORAGE_RUNNER: self.token}},
            session,
        )

    async def _attach_worker(self, browser: Browser, ext_id: str) -> str:
        prefix = f"chrome-extension://{ext_id}/"
        for _ in range(40):
            targets = (await browser.cdp.call("Target.getTargets")).get("targetInfos", [])
            for t in targets:
                if t.get("type") == "service_worker" and str(t.get("url", "")).startswith(prefix):
                    attached = await browser.cdp.call(
                        "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
                    )
                    return str(attached["sessionId"])
            await asyncio.sleep(0.25)
        raise CdpError("no apareció el service worker de la extensión")

    async def _has_session(self, browser: Browser) -> bool:
        """Si X tiene sesión en el perfil: la cookie está y ninguna pestaña quedó en el inicio
        de sesión (adonde X manda la búsqueda cuando la cookie no le sirve)."""
        targets = (await browser.cdp.call("Target.getTargets")).get("targetInfos", [])
        if any(t.get("type") == "page" and is_login_url(str(t.get("url", ""))) for t in targets):
            return False
        cookies = (await browser.cdp.call("Storage.getCookies")).get("cookies", [])
        return any(
            c.get("name") == SESSION_COOKIE and str(c.get("domain", "")).endswith("x.com")
            for c in cookies
        )

    async def _ensure_session(self, browser: Browser) -> bool:
        """Si el perfil tiene sesión de X. Si no, y hay cookie en el `.env` que todavía no se
        probó, la pone: si X la rechaza, la borra, y la próxima vuelta pide iniciar sesión."""
        if await self._has_session(browser):
            return True
        token = self._settings.x_auth_token
        if not token or self._env_cookie_tried:
            return False
        self._env_cookie_tried = True
        cookies = [_cookie(SESSION_COOKIE, token)]
        if self._settings.x_ct0:
            cookies.append(_cookie("ct0", self._settings.x_ct0, http_only=False))
        await browser.cdp.call("Storage.setCookies", {"cookies": cookies})
        log.info("navegador de xport: inicié la sesión de X con la cookie del .env")
        return True

    async def _login(self) -> None:
        """Abre el perfil en una ventana común de Chrome en el inicio de sesión de X y espera
        a que la sesión aparezca en sus cookies. Si la persona cierra la ventana sin
        iniciarla, se rinde hasta que alguien vuelva a encolar."""
        if self._browser is not None and self._browser.alive:
            # La sesión que haya quedado no sirve: se borra, así el inicio de sesión de X
            # arranca limpio y no rebota contra una cookie vencida.
            with contextlib.suppress(CdpError):
                await self._browser.cdp.call("Storage.clearCookies")
        await self._close_browser()
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            self._give_up(Note(key="runner.no_display"))
            return
        profile = self._settings.browser_profile_dir
        self._status = RunnerStatus(RunnerState.LOGIN, Note(key="runner.login"))
        self._notifier.post(text("notify.login"), text("notify.login_body"))
        try:
            window = await self._login_launcher(profile, LOGIN_URL)
        except OSError as exc:
            self._give_up(Note(key="runner.login_window_failed", params={"error": str(exc)}))
            return
        self._login_window = window
        deadline = time.monotonic() + self.LOGIN_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                closed = window.returncode is not None
                if await asyncio.to_thread(self._session_probe, profile):
                    log.info("navegador de xport: sesión de X iniciada")
                    self._status = RunnerStatus(RunnerState.IDLE)
                    self._crashes = 0
                    self._env_cookie_tried = True  # la del `.env` ya no hace falta
                    return
                if closed:
                    break
                await asyncio.sleep(self.LOGIN_POLL_S)
        finally:
            await close_login_window(window)
            self._login_window = None
        self._give_up(Note(key="runner.login_closed"))

    def _give_up(self, note: Note) -> None:
        failed = Note(key="runner.gave_up", params={"reason": note})
        log.warning("navegador de xport: %s", failed.render())
        self._status = RunnerStatus(RunnerState.FAILED, failed)

    async def _close_browser(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            await browser.close()


def _cookie(name: str, value: str, *, http_only: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": ".x.com",
        "path": "/",
        "secure": True,
        "httpOnly": http_only,
        "sameSite": "None",
        "expires": time.time() + 365 * 86_400,  # que sobreviva a cerrar el navegador
    }


def is_login_url(url: str) -> bool:
    """Si una pestaña está en el inicio de sesión de X."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host not in ("x.com", "twitter.com") and not host.endswith((".x.com", ".twitter.com")):
        return False
    return parts.path.startswith(
        ("/i/flow/login", "/i/flow/signup", "/i/jf/onboarding", "/login", "/logout")
    )
