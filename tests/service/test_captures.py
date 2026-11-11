from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.providers._fixtures import search_response, tweet_entry
from tests.service.conftest import make_settings
from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.domain.account import AccountCheck, AccountStatus
from tweet_extractor.i18n import t
from tweet_extractor.service import notify
from tweet_extractor.service.app import create_app, origin_allowed
from tweet_extractor.service.captures import EMPTY_NOTE, empty_note
from tweet_extractor.service.notify import Notifier

RANGE = {"since": "2023-01-01", "until": "2023-02-01"}  # los tweets de prueba son del 4/1/2023


class Sent:
    """Las notificaciones que el servicio habría mostrado."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str, Path | None]] = []

    async def __call__(self, title: str, body: str, path: Path | None = None) -> None:
        self.items.append((title, body, path))

    def wait(self, n: int) -> list[tuple[str, str, Path | None]]:
        for _ in range(200):
            if len(self.items) >= n:
                return self.items
            time.sleep(0.01)
        raise AssertionError(f"esperaba {n} notificaciones y llegaron {self.items}")


def _client(tmp_path: Path, sent: Sent) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path), notifier=Notifier(sent)))


def _ingest(client: TestClient, account: str, *ids: str) -> None:
    page = search_response([tweet_entry(i) for i in ids])
    r = client.post("/ingest", json={"account": account, "op": "SearchTimeline", "pages": [page]})
    assert r.status_code == 200, r.text


def _report(client: TestClient, cap_id: int, state: str, **extra: object) -> dict:
    r = client.post(f"/captures/{cap_id}/state", json={"state": state, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_dos_cuentas_encoladas_salen_en_dos_csv(tmp_path: Path) -> None:
    # Lo que hace la extensión, sin tocar nada en el medio: pide la siguiente, la crawlea
    # (las páginas entran por /ingest) y avisa que terminó; el servicio exporta y avisa.
    sent = Sent()
    with _client(tmp_path, sent) as client:
        r = client.post("/captures", json={"accounts": ["@Uno, dos"], **RANGE})
        assert r.status_code == 201
        uno, dos = r.json()
        assert (uno["account"], dos["account"], uno["state"]) == ("uno", "dos", "queued")

        first = client.post("/captures/next").json()
        assert (first["id"], first["state"]) == (uno["id"], "running")
        _ingest(client, "uno", "1", "2")
        (listed,) = [c for c in client.get("/captures").json() if c["id"] == uno["id"]]
        assert listed["captured"] == 2 and listed["oldest"] == "2023-01-04"
        assert listed["percent"] == 90  # del 4/1 al 1/2: 28 de 31 días
        assert listed["resume_at"] is None
        assert listed["last_page_at"] is not None
        # Mientras la primera sigue, la siguiente es la misma.
        assert client.post("/captures/next").json()["id"] == uno["id"]

        done = _report(client, uno["id"], "done")
        assert done["state"] == "done" and done["percent"] == 100
        assert done["csv"] == "uno_2023-01-01_2023-02-01.csv" and done["exported"] == 2
        csv = client.get(done["download_url"])
        assert csv.status_code == 200 and "texto 1" in csv.text

        second = client.post("/captures/next").json()
        assert second["id"] == dos["id"]
        _ingest(client, "dos", "3")
        assert _report(client, dos["id"], "done")["exported"] == 1
        assert client.post("/captures/next").status_code == 204

        title, body, path = sent.wait(2)[0]
        assert title == "Terminó la captura de @uno"
        assert body == "2 tweets en uno_2023-01-01_2023-02-01.csv"
        assert path is not None and path.name == done["csv"]


def test_sin_un_solo_tweet_lo_dice(tmp_path: Path) -> None:
    sent = Sent()
    with _client(tmp_path, sent) as client:
        (cap,) = client.post("/captures", json={"accounts": ["nadie"], **RANGE}).json()
        client.post("/captures/next")
        done = _report(client, cap["id"], "done")
        assert done["note"] == {"key": "note.empty", "params": {}}
        assert done["csv"] is None and done["download_url"] is None
        assert sent.wait(1)[0][1] == EMPTY_NOTE.render() == t("note.empty")
        assert client.get(f"/captures/{cap['id']}/csv").status_code == 404


USERS = json.loads(
    (Path(__file__).parents[1] / "mappers" / "fixtures" / "user_by_screen_name.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    ("case", "key", "says"),
    [
        ("not_found", "note.account_not_found", "no existe en X"),
        ("suspended", "note.account_suspended", "X suspendió la cuenta @nadie"),
        ("protected", "note.account_protected", "es una cuenta protegida"),
        ("active", "note.account_quiet", "existe y es pública"),
    ],
)
def test_sin_tweets_dice_por_que_segun_la_cuenta(
    tmp_path: Path, case: str, key: str, says: str
) -> None:
    # La extensión abre el perfil y manda el `UserByScreenName` crudo con el `done` (punto 10).
    sent = Sent()
    with _client(tmp_path, sent) as client:
        (cap,) = client.post("/captures", json={"accounts": ["nadie"], **RANGE}).json()
        client.post("/captures/next")
        done = _report(client, cap["id"], "done", user=USERS[case])
        assert done["note"] == {"key": key, "params": {"account": "nadie"}}
        assert done["csv"] is None
        assert says in sent.wait(1)[0][1]
        (listed,) = client.get("/captures").json()
        assert listed["note"]["key"] == key
        english = client.get("/captures", headers={"accept-language": "en"}).json()
        assert english[0]["note"] == listed["note"]  # se guarda la clave, no el texto


def test_cada_estado_de_cuenta_tiene_su_aviso() -> None:
    for status in AccountStatus:
        note = empty_note("nadie", AccountCheck(status=status, reason="Withheld"))
        assert note.known(), status
        assert "@nadie" in note.render("es") and "@nadie" in note.render("en")
    unavailable = empty_note("nadie", AccountCheck(status=AccountStatus.UNAVAILABLE))
    assert unavailable.render("es").endswith("(?).")


def test_una_respuesta_que_no_se_entiende_da_el_aviso_de_siempre(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sent = Sent()
    with _client(tmp_path, sent) as client:
        (cap,) = client.post("/captures", json={"accounts": ["nadie"], **RANGE}).json()
        client.post("/captures/next")
        raro = {"data": {}, "errors": [{"code": 88, "message": "Rate limit exceeded"}]}
        done = _report(client, cap["id"], "done", user=raro)
        assert done["note"] == {"key": "note.empty", "params": {}}
        assert sent.wait(1)[0][1] == t("note.empty")
    assert "UserByScreenName" in caplog.text and "Rate limit exceeded" in caplog.text


def test_con_tweets_la_cuenta_no_cambia_nada(tmp_path: Path) -> None:
    sent = Sent()
    with _client(tmp_path, sent) as client:
        (cap,) = client.post("/captures", json={"accounts": ["uno"], **RANGE}).json()
        client.post("/captures/next")
        _ingest(client, "uno", "1")
        done = _report(client, cap["id"], "done", user=USERS["suspended"])
        assert done["note"] is None and done["exported"] == 1


def test_un_corte_avisa_y_una_cancelacion_no(tmp_path: Path) -> None:
    sent = Sent()
    with _client(tmp_path, sent) as client:
        a, b = client.post("/captures", json={"accounts": ["a", "b"], **RANGE}).json()
        client.post("/captures/next")
        cut = _report(client, a["id"], "stopped", note="x.com cambió el formato de los datos")
        assert cut["state"] == "stopped"
        cancel = _report(client, b["id"], "stopped", note="La cancelaste.", by_user=True)
        assert cancel["state"] == "stopped"
        assert client.post("/captures/next").status_code == 204
        time.sleep(0.05)
        assert sent.items == [
            ("Se cortó la captura de @a", "x.com cambió el formato de los datos", None)
        ]
        assert client.post("/captures/999/state", json={"state": "done"}).status_code == 404


def test_esperando_a_x_sigue_sola(tmp_path: Path) -> None:
    # Punto 11 de docs/PLAN.md: X frenó la búsqueda. La captura queda «esperando a X hasta
    # las 18:40», sigue siendo la que va (la de atrás no arranca) y no se notifica: sigue sola.
    sent = Sent()
    with _client(tmp_path, sent) as client:
        a, _ = client.post("/captures", json={"accounts": ["a", "b"], **RANGE}).json()
        client.post("/captures/next")
        waiting = _report(client, a["id"], "waiting", resume_at="2026-11-10T21:40:00Z")
        assert waiting["state"] == "waiting"
        assert waiting["resume_at"] == "2026-11-10T21:40:00Z"
        assert client.post("/captures/next").json()["id"] == a["id"]
        back = _report(client, a["id"], "running")
        assert back["state"] == "running" and back["resume_at"] is None
        time.sleep(0.05)
        assert sent.items == []


def test_con_el_tope_lleno_no_empieza_ninguna(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    async def fill() -> None:
        async with SlidingWindowGate(
            settings.audit_db_path, settings.hard_cap, settings.window_s
        ) as g:
            await g.record(settings.hard_cap)

    asyncio.run(fill())
    with TestClient(create_app(settings, notifier=Notifier(Sent()))) as client:
        client.post("/captures", json={"accounts": ["a"], **RANGE})
        assert client.post("/captures/next").status_code == 204
        assert client.get("/captures").json()[0]["state"] == "queued"


@pytest.mark.parametrize("accounts", [["no válida"], ["nasa since:2020-01-01"], ["a" * 16]])
def test_solo_nombres_de_usuario_de_x(tmp_path: Path, accounts: list[str]) -> None:
    # La cuenta va tal cual a la búsqueda `from:<cuenta>`: otra cosa le sumaría operadores.
    with _client(tmp_path, Sent()) as client:
        assert client.post("/captures", json={"accounts": accounts, **RANGE}).status_code == 422


def test_los_pedidos_de_otro_sitio_no_cambian_nada(tmp_path: Path) -> None:
    with _client(tmp_path, Sent()) as client:
        body = {"accounts": ["a"], **RANGE}
        evil = client.post("/captures", json=body, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        assert client.get("/captures").json() == []
        assert client.post(
            "/captures", json=body, headers={"Origin": "http://testserver"}
        ).is_success
        ext = {"Origin": "chrome-extension://aiildgjidjmeilmjinaililpijenmodi"}
        assert client.post("/captures/next", headers=ext).is_success
        # Leer no cambia nada: un GET pasa (y el navegador no le deja ver la respuesta).
        assert client.get("/gate", headers={"Origin": "https://evil.example"}).status_code == 200


def test_origin_allowed() -> None:
    assert origin_allowed("POST", None, "localhost:8000")  # la CLI, curl
    assert origin_allowed("POST", "http://localhost:8000", "localhost:8000")
    assert origin_allowed("POST", "moz-extension://1234-abcd", "localhost:8000")
    assert not origin_allowed("POST", "http://localhost:3000", "localhost:8000")
    assert not origin_allowed("POST", "null", "localhost:8000")
    assert not origin_allowed("DELETE", "https://evil.example", "localhost:8000")
    assert origin_allowed("GET", "https://evil.example", "localhost:8000")


def test_la_pagina_y_el_tope_por_dia(tmp_path: Path) -> None:
    with _client(tmp_path, Sent()) as client:
        page = client.get("/")
        assert page.status_code == 200 and 'data-i18n="page.new"' in page.text
        assert client.get("/page/page.js").status_code == 200
        _ingest(client, "uno", "1", "2")
        assert client.get("/gate/days").json() == [{"day": date.today().isoformat(), "accessed": 2}]


async def test_sin_como_notificar_no_rompe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda _name: None)
    await notify.system_notify("Terminó la captura de @a", "2 tweets")  # no tira
