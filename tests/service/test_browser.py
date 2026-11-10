"""El navegador sin ventana que maneja el servicio (punto 13 de docs/PLAN.md), con un Chrome
falso: nunca se lanza un navegador de verdad."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.service.conftest import make_settings
from tweet_extractor.service import browser as mod
from tweet_extractor.service.app import create_app
from tweet_extractor.service.browser import (
    LOGIN_URL,
    STORAGE_RUNNER,
    Browser,
    BrowserRunner,
    RunnerState,
    browser_args,
    encode_message,
    find_browser,
    is_login_url,
    profile_has_session,
    virtual_display_env,
)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def terminate(self) -> None:
        self.returncode = 0


class FakeCdp:
    """Contesta como Chrome lo justo para el runner. `session` = si el perfil tiene la
    cookie de sesión de X; `login_after` = cuántas consultas de cookies hasta que aparece
    (la persona iniciando sesión en la ventana)."""

    def __init__(self, process: FakeProcess, *, session: bool, login_after: int | None) -> None:
        self.process = process
        self.page_url = "about:blank"
        self.session = session
        self.login_after = login_after
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def call(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]:
        params = params or {}
        self.calls.append((method, params, session))
        if method == "Extensions.loadUnpacked":
            return {"id": "ext"}
        if method == "Target.getTargets":
            sw = {"type": "service_worker", "url": "chrome-extension://ext/bg.js", "targetId": "sw"}
            page = {"type": "page", "url": self.page_url, "targetId": "p"}
            return {"targetInfos": [page, sw]}
        if method == "Target.attachToTarget":
            return {"sessionId": f"s-{params['targetId']}"}
        if method == "Storage.getCookies":
            if self.login_after is not None:
                self.login_after -= 1
                if self.login_after <= 0:
                    self.session = True
            cookies = [{"name": "auth_token", "domain": ".x.com"}] if self.session else []
            return {"cookies": [{"name": "guest_id", "domain": ".x.com"}, *cookies]}
        if method == "Storage.setCookies":
            self.session = True
            return {}
        if method == "Storage.clearCookies":
            self.session = False
            return {}
        if method == "Browser.close":
            self.process.returncode = 0
            return {}
        return {}


class FakeQueue:
    def __init__(self, pending: bool) -> None:
        self.pending = pending

    async def has_unfinished(self) -> bool:
        return self.pending


class FakeNotifier:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []

    def post(self, title: str, body: str, path: Path | None = None) -> None:
        self.posts.append((title, body))


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        pending: bool = True,
        session: bool = True,
        login_after: int | None = None,
        launch_error: bool = False,
        **settings: Any,
    ) -> None:
        ext = tmp_path / "ext"
        ext.mkdir()
        (ext / "manifest.json").write_text("{}")
        chrome = tmp_path / "chrome"
        chrome.write_text("")
        cage = tmp_path / "cage"
        cage.write_text("")
        settings.setdefault("xport_browser_cage", cage)
        settings.setdefault("x_auth_token", None)  # nunca la cookie de un `.env` de verdad
        settings.setdefault("x_ct0", None)
        cfg = make_settings(
            tmp_path,
            xport_browser=True,
            xport_browser_bin=chrome,
            xport_extension_dir=ext,
            browser_profile_dir=tmp_path / "browser",
            **settings,
        )
        self.queue = FakeQueue(pending)
        self.notifier = FakeNotifier()
        self.launches: list[tuple[bool, str]] = []
        self.browsers: list[FakeCdp] = []
        self.session = session
        self.login_after = login_after
        self.launch_error = launch_error
        self.closes_login_by_hand = False
        self.login_windows: list[FakeProcess] = []
        self.runner = BrowserRunner(
            cfg,
            self.queue,  # type: ignore[arg-type]
            self.notifier,
            launcher=self.launch,
            login_launcher=self.open_login,
            session_probe=self.probe,
        )
        self.runner.LOGIN_POLL_S = 0

    async def open_login(self, profile: Path, url: str) -> FakeProcess:
        """La ventana común de Chrome para iniciar sesión, sin canal de depuración."""
        self.launches.append((False, url))
        window = FakeProcess()
        if self.closes_login_by_hand:
            window.returncode = 0
        self.login_windows.append(window)
        return window

    def probe(self, profile: Path) -> bool:
        """La base de cookies del perfil: la sesión aparece a la `login_after`-ésima mirada."""
        if self.login_after is None:
            return False
        self.login_after -= 1
        if self.login_after <= 0:
            self.session = True  # queda en el perfil para la próxima vez
            return True
        return False

    async def launch(self, profile: Path, ext: Path, headless: bool, url: str) -> Browser:
        if self.launch_error:
            raise OSError("no está")
        self.launches.append((headless, url))
        process = FakeProcess()
        cdp = FakeCdp(process, session=self.session, login_after=None)
        self.browsers.append(cdp)
        return Browser(process=process, cdp=cdp, headless=headless)  # type: ignore[arg-type]


def test_encode_message_is_json_ended_by_a_null_byte() -> None:
    raw = encode_message(7, "Storage.getCookies", {}, "s1")
    assert raw.endswith(b"\0")
    assert json.loads(raw[:-1]) == {
        "id": 7,
        "method": "Storage.getCookies",
        "params": {},
        "sessionId": "s1",
    }
    assert "sessionId" not in json.loads(encode_message(1, "Browser.close", {}, None)[:-1])


def test_browser_args_headless_and_login_window(tmp_path: Path) -> None:
    headless = browser_args(tmp_path, headless=True, url="about:blank")
    assert "--headless" in headless and "--remote-debugging-pipe" in headless
    assert f"--user-data-dir={tmp_path}" in headless and headless[-1] == "about:blank"
    window = browser_args(tmp_path, headless=False, url=LOGIN_URL)
    assert "--headless" not in window and f"--app={LOGIN_URL}" in window


def test_hidden_inside_a_virtual_display_is_a_normal_chrome(tmp_path: Path) -> None:
    args = browser_args(tmp_path, headless=True, url="about:blank", virtual=True)
    assert "--headless" not in args and "--ozone-platform=wayland" in args
    env = virtual_display_env({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1", "HOME": "/h"})
    assert "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env  # nunca la pantalla real
    assert env["WLR_BACKENDS"] == "headless" and env["HOME"] == "/h"


def test_find_browser_explicit_and_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chrome = tmp_path / "chrome"
    chrome.write_text("")
    assert find_browser(chrome) == chrome
    assert find_browser(tmp_path / "no-esta") is None
    monkeypatch.setattr(
        mod.shutil, "which", lambda n: "/usr/bin/chromium" if n == "chromium" else None
    )
    assert find_browser(None) == Path("/usr/bin/chromium")
    monkeypatch.setattr(mod.shutil, "which", lambda n: None)
    assert find_browser(None) is None


async def test_starts_headless_loads_the_extension_and_marks_it(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.runner.tick(now=0)
    assert h.launches == [(True, "about:blank")]
    calls = h.browsers[0].calls
    assert calls[0] == ("Extensions.loadUnpacked", {"path": str(tmp_path / "ext")}, None)
    store = [c for c in calls if c[0] == "Extensions.setStorageItems"]
    assert store == [
        (
            "Extensions.setStorageItems",
            {"id": "ext", "storageArea": "local", "values": {STORAGE_RUNNER: h.runner.token}},
            "s-sw",  # desde el service worker de la extensión, no desde una pestaña
        )
    ]
    assert h.runner.status.state == RunnerState.RUNNING
    assert (tmp_path / "browser").stat().st_mode & 0o777 == 0o700


async def test_only_the_xport_browser_gets_the_queue_while_it_runs(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    assert h.runner.accepts(None) is False  # ya está a cargo, aunque todavía no arrancó
    await h.runner.tick(now=0)
    assert h.runner.accepts(h.runner.token) is True
    assert h.runner.accepts(None) is False
    assert h.runner.accepts("otra") is False


async def test_off_or_without_browser_the_queue_is_open_to_any_extension(tmp_path: Path) -> None:
    off = BrowserRunner(make_settings(tmp_path), FakeQueue(True), FakeNotifier())  # type: ignore[arg-type]
    assert off.status.state == RunnerState.OFF and off.accepts(None)
    no_ext = BrowserRunner(
        make_settings(tmp_path, xport_browser=True, xport_extension_dir=tmp_path / "nada"),
        FakeQueue(True),  # type: ignore[arg-type]
        FakeNotifier(),
    )
    assert no_ext.status.state == RunnerState.OFF and no_ext.accepts(None)
    chrome = tmp_path / "chrome"
    chrome.write_text("")
    ext = tmp_path / "ext2"
    ext.mkdir()
    (ext / "manifest.json").write_text("{}")
    no_cage = BrowserRunner(
        make_settings(
            tmp_path,
            xport_browser=True,
            xport_browser_bin=chrome,
            xport_extension_dir=ext,
            xport_browser_cage=tmp_path / "sin-cage",
        ),
        FakeQueue(True),  # type: ignore[arg-type]
        FakeNotifier(),
    )
    assert no_cage.status.state == RunnerState.OFF and "cage" in (no_cage.status.note or "")


async def test_closes_after_the_queue_stays_empty(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.runner.tick(now=0)
    h.queue.pending = False
    await h.runner.tick(now=10)
    assert h.browsers[0].process.returncode is None  # todavía no pasó el minuto
    await h.runner.tick(now=10 + BrowserRunner.IDLE_STOP_S)
    assert h.browsers[0].process.returncode == 0
    assert h.runner.status.state == RunnerState.IDLE


async def test_uses_the_env_cookie_when_the_profile_has_no_session(tmp_path: Path) -> None:
    h = Harness(tmp_path, session=False, x_auth_token="tok", x_ct0="c")
    await h.runner.tick(now=0)
    sets = [c[1] for c in h.browsers[0].calls if c[0] == "Storage.setCookies"]
    assert [c["name"] for c in sets[0]["cookies"]] == ["auth_token", "ct0"]
    assert sets[0]["cookies"][0]["domain"] == ".x.com" and sets[0]["cookies"][0]["httpOnly"]
    assert h.runner.status.state == RunnerState.RUNNING


async def test_without_session_opens_the_login_window_and_then_goes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    h = Harness(tmp_path, session=False, login_after=2)
    await h.runner.tick(now=0)
    assert h.launches == [(True, "about:blank"), (False, LOGIN_URL)]
    assert h.notifier.posts and "sesión de X" in h.notifier.posts[0][0]
    assert h.runner.status.state == RunnerState.IDLE  # ya tiene sesión: la próxima vuelta sigue
    assert h.login_windows[0].returncode == 0  # la ventana se cerró sola
    await h.runner.tick(now=1)
    assert h.launches[-1] == (True, "about:blank")
    assert h.runner.status.state == RunnerState.RUNNING


async def test_closing_the_login_window_gives_the_queue_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    h = Harness(tmp_path, session=False, login_after=None)
    h.closes_login_by_hand = True
    await h.runner.tick(now=0)
    assert h.runner.status.state == RunnerState.FAILED
    assert h.runner.accepts(None)  # mientras tanto, la extensión de siempre
    h.runner.wake()  # alguien encoló otra vez
    assert h.runner.status.state == RunnerState.IDLE


async def test_gives_up_after_repeated_failures_and_wakes_up_on_a_new_capture(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, launch_error=True)
    for t in range(BrowserRunner.MAX_CRASHES):
        await h.runner.tick(now=t)
    assert h.runner.status.state == RunnerState.FAILED
    assert h.runner.accepts(None)
    assert h.notifier.posts
    h.launch_error = False
    h.runner.wake()
    await h.runner.tick(now=100)
    assert h.runner.status.state == RunnerState.RUNNING


def test_next_capture_skips_other_browsers_while_xport_runs_its_own(tmp_path: Path) -> None:
    class Stub:
        def accepts(self, token: str | None) -> bool:
            return token == "secreto"

        def wake(self) -> None:
            pass

    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        app.state.svc.runner = Stub()
        body = {"accounts": ["alguien"], "since": "2026-01-01", "until": "2026-02-01"}
        assert client.post("/captures", json=body).status_code == 201
        assert client.post("/captures/next").status_code == 204
        ok = client.post("/captures/next", headers={"x-xport-runner": "secreto"})
        assert ok.status_code == 200 and ok.json()["account"] == "alguien"


def test_browser_status_endpoint(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/browser").json()["state"] == "off"


@pytest.mark.parametrize(
    ("url", "login"),
    [
        ("https://x.com/i/flow/login?redirect_after_login=%2Fsearch", True),
        ("https://x.com/login", True),
        ("https://x.com/i/jf/onboarding/web?mode=login", True),
        ("https://x.com/search?q=from%3Aalguien&f=live", False),
        ("https://example.com/i/flow/login", False),
        ("about:blank", False),
    ],
)
def test_is_login_url(url: str, login: bool) -> None:
    assert is_login_url(url) is login


async def test_a_search_bounced_to_the_login_opens_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La cookie está, pero X no la acepta y manda la búsqueda al inicio de sesión."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    h = Harness(tmp_path, session=True, login_after=1)
    await h.runner.tick(now=0)
    assert h.runner.status.state == RunnerState.RUNNING
    h.browsers[0].page_url = "https://x.com/i/flow/login?redirect_after_login=%2Fsearch"
    await h.runner.tick(now=BrowserRunner.SESSION_CHECK_S)
    assert ("Storage.clearCookies", {}, None) in h.browsers[0].calls
    assert h.launches[-1] == (False, LOGIN_URL)


async def test_a_page_stuck_on_a_chrome_error_restarts_the_browser(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.runner.tick(now=0)
    h.browsers[0].page_url = "chrome-error://chromewebdata/"
    await h.runner.tick(now=BrowserRunner.SESSION_CHECK_S)  # una vez puede ser un parpadeo
    assert h.browsers[0].process.returncode is None
    await h.runner.tick(now=2 * BrowserRunner.SESSION_CHECK_S)
    assert h.browsers[0].process.returncode == 0  # cerrado: la próxima vuelta abre otro
    await h.runner.tick(now=2 * BrowserRunner.SESSION_CHECK_S + 1)
    assert len(h.launches) == 2 and h.runner.status.state == RunnerState.RUNNING


def test_profile_has_session_reads_only_whether_the_cookie_exists(tmp_path: Path) -> None:
    import sqlite3

    assert profile_has_session(tmp_path) is False  # sin perfil todavía
    (tmp_path / "Default").mkdir()
    con = sqlite3.connect(tmp_path / "Default" / "Cookies")
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
    con.execute("INSERT INTO cookies VALUES ('.x.com', 'guest_id', x'00')")
    con.commit()
    assert profile_has_session(tmp_path) is False
    con.execute("INSERT INTO cookies VALUES ('.x.com', 'auth_token', x'00')")
    con.commit()
    con.close()
    assert profile_has_session(tmp_path) is True
    assert not (tmp_path / ".xport-sesion").exists()  # la copia no queda


async def test_each_start_drops_the_cached_extension_worker(tmp_path: Path) -> None:
    """Si no, Chrome sigue corriendo el background de un armado anterior."""
    cache = tmp_path / "browser" / "Default" / "Service Worker" / "ScriptCache"
    cache.mkdir(parents=True)
    (cache / "viejo").write_text("background de antes")
    h = Harness(tmp_path)
    await h.runner.tick(now=0)
    assert h.runner.status.state == RunnerState.RUNNING
    assert not (tmp_path / "browser" / "Default" / "Service Worker").exists()
