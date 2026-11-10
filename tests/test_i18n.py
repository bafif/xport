"""Castellano e inglés: los catálogos están completos, el idioma se detecta como en todas las
apps de xport y lo que ve la persona sale del catálogo.

Los catálogos son `src/tweet_extractor/locales/{es,en}.json` (CLI, servicio y página local) y
`extension/locales/{es,en}.json` (la extensión, que también tiene sus propios tests).
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import string
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.service.conftest import make_settings
from tweet_extractor import i18n
from tweet_extractor.i18n import Note, accept_lang, catalog, parse_lang, system_lang, t
from tweet_extractor.service.app import create_app
from tweet_extractor.storage.queue import CaptureState

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tweet_extractor"
PAGE = SRC / "service" / "page"
EXTENSION_LOCALES = ROOT / "extension" / "locales"

# Un texto para leer: tres palabras seguidas, o una letra con tilde o eñe.
PROSE = re.compile(
    r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}|[áéíóúñÑ¿¡]"
)


def _fields(text: str) -> set[str]:
    return {f.split(":")[0] for _, f, _, _ in string.Formatter().parse(text) if f}


# ── los catálogos ──


def test_los_dos_idiomas_tienen_las_mismas_claves() -> None:
    es, en = catalog("es"), catalog("en")
    assert set(es) - set(en) == set(), "les falta el inglés"
    assert set(en) - set(es) == set(), "les falta el castellano"


@pytest.mark.parametrize("key", sorted(catalog("es")))
def test_cada_texto_tiene_los_mismos_valores_en_los_dos(key: str) -> None:
    es, en = catalog("es")[key], catalog("en")[key]
    assert es.strip() and en.strip(), f"{key} está vacío"
    assert _fields(es) == _fields(en), f"{key}: {{valores}} distintos"


def _python_keys() -> set[str]:
    """Las claves que pide el código: `t(...)`, `text(...)`, `_invalid(...)`, `Note(key=...)`."""
    keys: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in ("t", "text", "_invalid") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
            if name == "Note":
                for kw in node.keywords:
                    if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                        keys.add(str(kw.value.value))
    return keys


def test_las_claves_del_codigo_estan_en_el_catalogo() -> None:
    keys = _python_keys()
    assert len(keys) > 50
    assert keys - set(catalog("es")) == set()
    for status in (401, 402, 403):  # `t(f"provider.api_{status}")`
        assert f"provider.api_{status}" in catalog("es")


def test_los_avisos_de_la_extension_son_los_mismos_que_los_del_servicio() -> None:
    # La extensión manda al servicio el motivo de un corte (`note.stop_cap`…) y lo muestra en
    # el popup; el servicio lo muestra en la página y en la notificación: el mismo texto.
    for lang in i18n.LANGS:
        ext = json.loads((EXTENSION_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        notes = {k: v for k, v in ext.items() if k.startswith("note.")}
        assert notes
        for key, value in notes.items():
            assert catalog(lang).get(key) == value, f"{key} ({lang}) no coincide con el servicio"


# ── la página local ──


def _page_keys() -> set[str]:
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    js = (PAGE / "page.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-aria-label)?="([^"]+)"', html))
    keys |= set(re.findall(r"\bt\('([a-z][a-z0-9_.]*)'", js))
    keys |= {f"state.{s.value}" for s in CaptureState}  # t(`state.${c.state}`)
    keys |= {f"page.browser_{s}" for s in ("idle", "running", "login")}  # t(`page.browser_…`)
    return keys


def test_las_claves_de_la_pagina_estan_en_el_catalogo() -> None:
    keys = _page_keys()
    assert len(keys) > 40
    assert keys - set(catalog("es")) == set()


class _LooseText(HTMLParser):
    """Junta el texto visible que no está en un elemento con `data-i18n`."""

    VOID = {"meta", "link", "input", "br", "img", "hr"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[bool] = []
        self.loose: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return
        names = dict(attrs)
        self.stack.append("data-i18n" in names or tag in ("script", "style", "title"))

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and text != "xport" and not any(self.stack):
            self.loose.append(text)


def test_la_pagina_no_tiene_textos_fuera_del_catalogo() -> None:
    parser = _LooseText()
    parser.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    assert parser.loose == []

    js = (PAGE / "page.js").read_text(encoding="utf-8")
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", js)
    literals = re.findall(r"'((?:\\.|[^'\\\n])*)'|`((?:\\.|[^`\\])*)`|\"((?:\\.|[^\"\\\n])*)\"", js)
    prose = [s for groups in literals for s in groups if s and PROSE.search(s)]
    assert prose == [], "textos a mano en page.js: van al catálogo"


# ── el idioma ──


@pytest.mark.parametrize(
    ("env", "lang"),
    [
        ({"LANG": "es_AR.UTF-8"}, "es"),
        ({"LANG": "es_ES"}, "es"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "pt_BR.UTF-8"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({}, "en"),
        ({"LC_ALL": "en_US.UTF-8", "LANG": "es_AR.UTF-8"}, "en"),
        ({"LC_MESSAGES": "es_MX.UTF-8", "LANG": "en_US.UTF-8"}, "es"),
        ({"LANGUAGE": "fr:es", "LANG": "fr_FR.UTF-8"}, "es"),  # alguno de los preferidos
        ({"XPORT_LANG": "en", "LANG": "es_AR.UTF-8"}, "en"),  # elegido a mano
        ({"XPORT_LANG": "es", "LANG": "en_US.UTF-8"}, "es"),
        ({"XPORT_LANG": "fr", "LANG": "es_AR.UTF-8"}, "es"),  # uno que no hay: se ignora
    ],
)
def test_el_idioma_del_sistema(env: dict[str, str], lang: str) -> None:
    assert system_lang(env) == lang


@pytest.mark.parametrize(
    ("header", "lang"),
    [
        ("es-AR,es;q=0.9,en;q=0.8", "es"),
        ("en-US,en;q=0.9,es;q=0.8", "es"),
        ("en-US,en;q=0.9", "en"),
        ("es-419", "es"),
        ("es;q=0", None),
        ("de", "en"),
        ("", None),
        (None, None),
    ],
)
def test_el_idioma_de_un_pedido(header: str | None, lang: str | None) -> None:
    assert accept_lang(header) == lang


def test_un_idioma_elegido_a_mano() -> None:
    assert parse_lang("EN") == "en" and parse_lang("es-AR") == "es" and parse_lang("fr") is None


def test_numeros_en_cada_idioma() -> None:
    assert i18n.number(1234567, "es") == "1.234.567"
    assert i18n.number(1234567, "en") == "1,234,567"


# ── los avisos guardados ──


def test_un_aviso_se_lee_en_el_idioma_de_quien_lo_lee() -> None:
    note = Note(key="runner.crashed", params={"reason": Note(key="runner.closed"), "count": 3})
    again = Note.load(note.dump())
    assert again == note
    assert again is not None and again.render("es").startswith("El navegador de xport se cerró")
    assert again.render("en") == (
        "xport's browser closed on its own. It failed 3 times in a row, so captures go back to "
        "your browser."
    )


def test_un_aviso_viejo_en_texto_sigue_saliendo() -> None:
    old = Note.load("La cancelaste.")
    assert old is not None and old.render("en") == "La cancelaste."
    assert Note.load(None) is None


# ── la CLI ──


def test_la_cli_con_lang_en() -> None:
    from tweet_extractor import cli

    result = CliRunner().invoke(
        cli.app,
        [
            "capture",
            "--lang",
            "en",
            "-a",
            "nasa since:2020",
            "--since",
            "2023-01-01",
            "--until",
            "2023-01-08",
        ],
    )
    assert result.exit_code != 0
    assert "isn't an X username" in str(result.output) + str(getattr(result, "stderr", ""))


def test_la_ayuda_de_la_cli_en_ingles(monkeypatch: pytest.MonkeyPatch) -> None:
    # La ayuda de Typer se arma al importar: `--lang` se lee antes, de la línea de comandos.
    from tweet_extractor import cli

    try:
        monkeypatch.setattr(sys, "argv", ["tweet-extractor", "--lang", "en", "capture"])
        importlib.reload(cli)
        result = CliRunner().invoke(cli.app, ["--lang", "en", "capture", "--help"])
        assert "Queues captures for the extension" in result.output
        monkeypatch.setattr(sys, "argv", ["tweet-extractor", "--lang", "es"])
        importlib.reload(cli)
        result = CliRunner().invoke(cli.app, ["capture", "--help"])
        assert "Encola capturas para la extensión" in result.output
    finally:
        monkeypatch.setattr(sys, "argv", ["tweet-extractor"])
        importlib.reload(cli)


# ── el servicio ──


def test_los_errores_salen_en_el_idioma_del_pedido(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        en = client.get("/captures/999/csv", headers={"accept-language": "en-US,en;q=0.9"})
        es = client.get("/captures/999/csv", headers={"accept-language": "es-AR"})
        assert en.status_code == es.status_code == 404
        assert en.json()["detail"] == "that capture has no CSV"
        assert es.json()["detail"] == "esa captura no tiene CSV"

        body = {"accounts": ["no-vale"], "since": "2023-01-01", "until": "2023-02-01"}
        bad = client.post("/captures", json=body, headers={"accept-language": "en"})
        assert bad.status_code == 422
        assert bad.json()["detail"][0]["msg"].startswith("@no-vale isn't an X username")
        bad = client.post("/captures", json=body, headers={"accept-language": "es"})
        assert bad.json()["detail"][0]["msg"].startswith("@no-vale no es un nombre de usuario")


def test_la_pagina_pide_sus_textos(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/i18n/en").json() == catalog("en")
        assert client.get("/i18n/es").json()["page.capture"] == "Capturar"
        assert client.get("/i18n/fr").status_code == 422


def test_un_aviso_que_no_existe_no_entra(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        (cap,) = client.post(
            "/captures", json={"accounts": ["a"], "since": "2023-01-01", "until": "2023-02-01"}
        ).json()
        r = client.post(
            f"/captures/{cap['id']}/state", json={"state": "stopped", "note": {"key": "nope"}}
        )
        assert r.status_code == 422
        r = client.post(
            f"/captures/{cap['id']}/state",
            json={
                "state": "stopped",
                "note": {"key": "note.stop_rejected", "params": {"status": 413}},
            },
        )
        assert r.status_code == 200
        assert r.json()["note"] == {"key": "note.stop_rejected", "params": {"status": 413}}
        assert t("note.stop_rejected", "en", status=413).startswith("The service rejected")
