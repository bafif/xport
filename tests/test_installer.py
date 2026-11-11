"""El instalador (punto 14 de docs/PLAN.md), con un paquete falso y un HOME temporal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tweet_extractor import installer
from tweet_extractor.config import Settings
from tweet_extractor.installer import InstallError, Layout
from tweet_extractor.launcher import LaunchError


def _bundle(tmp_path: Path, version: str = "9.9.9", extra: str = "uno") -> Path:
    root = tmp_path / "pkg" / "xport"
    (root / "extension").mkdir(parents=True, exist_ok=True)
    (root / "extension" / "manifest.json").write_text("{}")
    (root / "xport-bundle.json").write_text(json.dumps({"version": version}))
    (root / "xport").write_text("#!/bin/sh\n")
    (root / "xport.png").write_bytes(b"png")
    (root / f"{extra}.txt").write_text(extra)
    return root


def _no_stop(settings: Settings, port: int) -> bool:
    return False


def _install(tmp_path: Path, source: Path, **kw: object) -> Layout:
    home = tmp_path / "home"
    env = {"PATH": "/usr/bin"}
    kw.setdefault("start", False)
    kw.setdefault("stopper", _no_stop)
    installer.install(source, platform="linux", home=home, env=env, **kw)  # type: ignore[arg-type]
    return installer.layout("linux", home, env)


def test_instala_en_linux(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lay = _install(tmp_path, _bundle(tmp_path))
    assert lay.app == tmp_path / "home" / ".local" / "lib" / "xport"
    assert (lay.app / "xport-bundle.json").is_file()
    assert lay.command is not None and lay.menu_entry is not None
    assert lay.command.read_text() == f'#!/bin/sh\nexec {lay.app / "xport"} "$@"\n'
    assert lay.command.stat().st_mode & 0o111
    entry = lay.menu_entry.read_text()
    assert f'Exec="{lay.app / "xport"}"' in entry
    assert f"Icon={lay.app / 'xport.png'}" in entry
    assert "Comment[es]=Exporta a CSV" in entry
    out = capsys.readouterr().out
    assert "xport 9.9.9 quedó instalado" in out
    assert "no está en tu PATH" in out
    assert "Tus datos:" in out


def test_reinstalar_reemplaza_la_version_anterior(tmp_path: Path) -> None:
    lay = _install(tmp_path, _bundle(tmp_path, "1.0", "vieja"))
    assert (lay.app / "vieja.txt").exists()
    (tmp_path / "pkg").rename(tmp_path / "pkg-1.0")
    lay = _install(tmp_path, _bundle(tmp_path, "2.0", "nueva"))
    assert (lay.app / "nueva.txt").exists()
    assert not (lay.app / "vieja.txt").exists()
    assert sorted(p.name for p in lay.app.parent.iterdir()) == ["xport"]  # sin .new ni .old


def test_cierra_el_que_corre_y_arranca_el_nuevo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[list[str]] = []
    lay = _install(
        tmp_path,
        _bundle(tmp_path),
        start=True,
        stopper=lambda settings, port: True,
        starter=lambda cmd: started.append(cmd) or 0,
    )
    assert started == [[str(lay.app / "xport")]]
    out = capsys.readouterr().out
    assert "Cerré el xport que estaba corriendo" in out


def test_si_no_puede_cerrar_el_viejo_sigue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def stuck(settings: Settings, port: int) -> bool:
        raise LaunchError("sin pid")

    lay = _install(tmp_path, _bundle(tmp_path), stopper=stuck)
    assert (lay.app / "xport-bundle.json").is_file()
    assert "sin pid" in capsys.readouterr().out


def test_si_no_arranca_lo_dice(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="no pudo arrancar"):
        _install(tmp_path, _bundle(tmp_path), start=True, starter=lambda cmd: 1)


def test_solo_desde_el_paquete(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="xport-bundle.json"):
        _install(tmp_path, tmp_path)
    with pytest.raises(InstallError):
        _install(tmp_path, None)  # type: ignore[arg-type]


def test_en_macos_no_hay_instalador(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="xport.app"):
        installer.layout("darwin", tmp_path, {})


def test_desinstalar_deja_los_datos(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lay = _install(tmp_path, _bundle(tmp_path))
    data = Settings().xport_data_dir
    data.mkdir(parents=True)
    (data / "tweets.db").write_text("")
    installer.uninstall(platform="linux", home=tmp_path / "home", env={}, stopper=_no_stop)
    assert not lay.app.exists()
    assert lay.command is not None and not lay.command.exists()
    assert lay.menu_entry is not None and not lay.menu_entry.exists()
    assert (data / "tweets.db").exists()
    assert str(data) in capsys.readouterr().out


def test_lugares_en_windows(tmp_path: Path) -> None:
    env = {
        "LOCALAPPDATA": r"C:\Users\ana\AppData\Local",
        "APPDATA": r"C:\Users\ana\AppData\Roaming",
    }
    lay = installer.layout("win32", tmp_path, env)
    assert lay.app == Path(env["LOCALAPPDATA"]) / "Programs" / "xport"
    assert lay.start_menu is not None and lay.start_menu.name == "xport.lnk"
    assert "Start Menu" in str(lay.start_menu)
    cmd = installer.launcher_command(lay.app, "win32")
    assert cmd[0].endswith("pythonw.exe") and cmd[1:] == ["-I", "-m", "tweet_extractor.launcher"]


def test_xdg_data_home(tmp_path: Path) -> None:
    lay = installer.layout("linux", tmp_path, {"XDG_DATA_HOME": "/datos"})
    assert lay.menu_entry == Path("/datos/applications/xport.desktop")
    assert installer.layout("linux", tmp_path, {"XDG_DATA_HOME": "relativa"}).menu_entry == (
        tmp_path / ".local/share/applications/xport.desktop"
    )


def test_rutas_raras_en_los_accesos(tmp_path: Path) -> None:
    app = Path('/home/ana $ "x"/lib/xport')
    assert installer.command_script(app) == (
        '#!/bin/sh\nexec \'/home/ana $ "x"/lib/xport/xport\' "$@"\n'
    )
    exec_line = next(
        line for line in installer.desktop_entry(app).splitlines() if line.startswith("Exec=")
    )
    assert exec_line == 'Exec="/home/ana \\\\$ \\\\"x\\\\"/lib/xport/xport"'
