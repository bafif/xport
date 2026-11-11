"""Las partes puras de `deploy/bundle/build.py` (el armado entero se prueba en CI, con
`--smoke`, en Linux, Windows y macOS)."""

from __future__ import annotations

import importlib.util
import struct
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "xport_bundle_build", ROOT / "deploy/bundle/build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _build()


def test_la_marca_es_la_misma_que_busca_el_servicio() -> None:
    from tweet_extractor.config import BUNDLE_MARK

    assert build.MARK == BUNDLE_MARK


def test_el_desinstalador_de_windows_sale_de_su_carpeta() -> None:
    script = build.cmd_script("tweet_extractor.installer", "uninstall", pause=True, leave_dir=True)
    lines = script.splitlines()
    assert lines[0] == "@echo off"
    assert lines[1] == 'cd /d "%TEMP%"'
    assert lines[2] == ('"%~dp0python\\python.exe" -I -m tweet_extractor.installer uninstall %*')
    assert lines[3] == "pause"
    assert "cd /d" not in build.cmd_script("tweet_extractor.launcher")


def test_el_lanzador_de_linux_usa_su_python() -> None:
    script = build.posix_script("tweet_extractor.launcher")
    assert script.startswith("#!/bin/sh\n")
    assert 'exec "$here/python/bin/python3" -I -m tweet_extractor.launcher "$@"' in script


def test_nombres_de_los_paquetes(tmp_path: Path) -> None:
    top = tmp_path / "work" / "xport"
    top.mkdir(parents=True)
    (top / "xport-bundle.json").write_text("{}")
    out = tmp_path / "dist" / "xport-0.2.1-windows-x86_64"
    zipped = build.archive(top, out, "windows")
    assert zipped.name == "xport-0.2.1-windows-x86_64.zip"
    assert "xport/xport-bundle.json" in zipfile.ZipFile(zipped).namelist()
    tarred = build.archive(top, tmp_path / "dist" / "xport-0.2.1-linux-x86_64", "linux")
    assert tarred.name == "xport-0.2.1-linux-x86_64.tar.gz"
    assert "xport/xport-bundle.json" in tarfile.open(tarred).getnames()


def test_iconos_con_el_png_adentro() -> None:
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 128, 128)
    ico = build.png_to_ico(png)
    assert struct.unpack("<HHH", ico[:6]) == (0, 1, 1)
    assert ico[6] == 128 and ico.endswith(png)
    icns = build.png_to_icns(png)
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)
