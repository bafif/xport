"""El instalador de xport (punto 14 de docs/PLAN.md). Lo corre el Python propio del paquete
(`install.sh` en Linux, `install.cmd` en Windows) y deja xport instalado para esta persona, sin
pedir permisos de administrador ni que tenga Python o uv.

- Copia la carpeta del paquete a su lugar: `~/.local/lib/xport` en Linux,
  `%LOCALAPPDATA%\\Programs\\xport` en Windows. Si ya había una, la reemplaza entera; antes
  cierra el xport que esté corriendo.
- Arma el acceso directo «xport»: en Linux, en el menú de aplicaciones (`.desktop`) y como
  comando en `~/.local/bin`; en Windows, en el menú Inicio y en el escritorio.
- Muestra lo que falta para capturar sin ventana (lo mismo que `xport check`), arranca xport y
  abre su página, que guía el primer inicio de sesión en X.

`uninstall` saca todo eso y deja los datos (el registro del tope, los tweets, los CSV y el perfil
del navegador de xport) en su carpeta, que es la misma con o sin instalador.

En macOS no hay instalador: `xport.app` se arrastra a Aplicaciones. Lo de Windows no está probado
en una máquina con Windows todavía.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tweet_extractor.config import BUNDLE_MARK, BUNDLE_ROOT, Settings
from tweet_extractor.i18n import LANGS, argv_lang, parse_lang, set_lang, system_lang, t
from tweet_extractor.launcher import (
    LaunchError,
    port_from_env,
    print_requirements,
    safe_console,
    stop,
)

ICON = "xport.png"
WINDOWS_ICON = "xport.ico"


class InstallError(RuntimeError):
    """No se puede instalar o desinstalar. El mensaje ya está en el idioma de la persona."""


@dataclass(frozen=True)
class Layout:
    """Dónde queda cada cosa en este sistema."""

    app: Path  # la carpeta del paquete instalada
    command: Path | None = None  # Linux: el comando `xport`
    menu_entry: Path | None = None  # Linux: el acceso directo del menú de aplicaciones
    start_menu: Path | None = None  # Windows: el acceso directo del menú Inicio


def layout(platform: str, home: Path, env: Mapping[str, str]) -> Layout:
    if platform == "win32":
        local = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData/Local"
        roaming = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData/Roaming"
        programs = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        return Layout(app=local / "Programs" / "xport", start_menu=programs / "xport.lnk")
    if platform.startswith("linux"):
        xdg = env.get("XDG_DATA_HOME", "")
        share = Path(xdg) if xdg.startswith("/") else home / ".local" / "share"
        return Layout(
            app=home / ".local" / "lib" / "xport",
            command=home / ".local" / "bin" / "xport",
            menu_entry=share / "applications" / "xport.desktop",
        )
    raise InstallError(t("installer.unsupported"))


def launcher_command(app: Path, platform: str) -> list[str]:
    """Cómo se abre xport instalado: lo mismo que el acceso directo."""
    if platform == "win32":
        return [str(app / "python" / "pythonw.exe"), "-I", "-m", "tweet_extractor.launcher"]
    return [str(app / "xport")]


def replace_dir(src: Path, dest: Path) -> None:
    """Copia `src` a `dest` y reemplaza lo que hubiera. Copia primero al lado (`xport.new`) y
    recién con la copia entera cambia una carpeta por la otra: una copia cortada a la mitad no
    deja un xport roto."""
    new = dest.with_name(dest.name + ".new")
    old = dest.with_name(dest.name + ".old")
    for leftover in (new, old):
        shutil.rmtree(leftover, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, new, symlinks=True)
    if dest.exists():
        dest.rename(old)
    new.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


def command_script(app: Path) -> str:
    return f'#!/bin/sh\nexec {shlex.quote(str(app / "xport"))} "$@"\n'


def _desktop_exec(path: Path) -> str:
    """Un argumento del `Exec` de un `.desktop`, entre comillas y con los escapes que pide la
    especificación (primero los del argumento y después los del valor de texto)."""
    quoted = str(path)
    for ch in ("\\", '"', "`", "$"):
        quoted = quoted.replace(ch, "\\" + ch)
    return '"' + quoted.replace("\\", "\\\\") + '"'


def desktop_entry(app: Path) -> str:
    return "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            "Name=xport",
            f"Comment={t('installer.desktop_comment', 'en')}",
            f"Comment[es]={t('installer.desktop_comment', 'es')}",
            f"Exec={_desktop_exec(app / 'xport')}",
            f"Icon={app / ICON}",
            "Terminal=false",
            "StartupNotify=false",
            "Categories=Network;",
            "",
        )
    )


# Los accesos directos de Windows (`.lnk`) se arman con el objeto COM de WScript, que está en
# todo Windows. Los valores van por variables de entorno: nada se interpreta como código.
_PS_SHORTCUTS = "\n".join(
    (
        "$shell = New-Object -ComObject WScript.Shell",
        "$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'xport.lnk'",
        "foreach ($path in @($env:XPORT_START_MENU, $desktop)) {",
        "  if ($env:XPORT_REMOVE) {",
        "    Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue",
        "    continue",
        "  }",
        "  New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null",
        "  $link = $shell.CreateShortcut($path)",
        "  $link.TargetPath = $env:XPORT_TARGET",
        "  $link.Arguments = '-I -m tweet_extractor.launcher'",
        "  $link.WorkingDirectory = $env:XPORT_APP",
        "  $link.IconLocation = $env:XPORT_ICON",
        "  $link.Description = $env:XPORT_DESCRIPTION",
        "  $link.Save()",
        "}",
    )
)


def windows_shortcuts(lay: Layout, *, remove: bool = False) -> None:
    assert lay.start_menu is not None
    env = dict(
        os.environ,
        XPORT_START_MENU=str(lay.start_menu),
        XPORT_TARGET=str(lay.app / "python" / "pythonw.exe"),
        XPORT_APP=str(lay.app),
        XPORT_ICON=str(lay.app / WINDOWS_ICON),
        XPORT_DESCRIPTION=t("installer.desktop_comment"),
        XPORT_REMOVE="1" if remove else "",
    )
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SHORTCUTS],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 and not remove:
        error = done.stderr.strip() or f"powershell: {done.returncode}"
        raise InstallError(t("installer.shortcut_failed", error=error, path=lay.app))


def _write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def make_shortcuts(lay: Layout, env: Mapping[str, str]) -> None:
    if lay.start_menu is not None:
        windows_shortcuts(lay)
        print(t("installer.shortcut", path=lay.start_menu))
        return
    assert lay.command is not None and lay.menu_entry is not None
    _write(lay.command, command_script(lay.app), 0o755)
    _write(lay.menu_entry, desktop_entry(lay.app), 0o644)
    updater = shutil.which("update-desktop-database")
    if updater is not None:
        subprocess.run([updater, str(lay.menu_entry.parent)], capture_output=True, check=False)
    print(t("installer.shortcut", path=lay.menu_entry))
    path_dirs = [Path(d) for d in env.get("PATH", "").split(os.pathsep) if d]
    if lay.command.parent not in path_dirs:
        print(t("installer.not_in_path", dir=lay.command.parent, path=lay.command))
    else:
        print(t("installer.command", path=lay.command))


def remove_shortcuts(lay: Layout) -> None:
    if lay.start_menu is not None:
        windows_shortcuts(lay, remove=True)
        return
    for path in (lay.command, lay.menu_entry):
        if path is not None:
            path.unlink(missing_ok=True)


def _close_running(env: Mapping[str, str], stopper: Stopper) -> None:
    """Cierra el xport que esté corriendo: sus archivos se van a reemplazar o borrar."""
    try:
        if stopper(port_from_env(env)):
            print(t("installer.stopped_old"))
    except LaunchError as e:
        print(t("installer.could_not_stop", error=e))


Stopper = Callable[[int], bool]
Starter = Callable[[list[str]], int]


def _start(cmd: list[str]) -> int:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, check=False).returncode


def install(
    source: Path | None = BUNDLE_ROOT,
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    start: bool = True,
    stopper: Stopper = stop,
    starter: Starter = _start,
) -> None:
    if source is None or not (source / BUNDLE_MARK).is_file():
        raise InstallError(t("installer.not_bundle", mark=BUNDLE_MARK))
    env = os.environ if env is None else env
    lay = layout(platform, Path.home() if home is None else home, env)
    _close_running(env, stopper)
    if source.resolve() != lay.app.resolve():
        replace_dir(source, lay.app)
    version = json.loads((lay.app / BUNDLE_MARK).read_text(encoding="utf-8")).get("version", "?")
    print(t("installer.installed", version=version, path=lay.app))
    make_shortcuts(lay, env)
    print()
    print_requirements(Settings(xport_extension_dir=lay.app / "extension"))
    print()
    if start:
        print(t("installer.starting"))
        if starter(launcher_command(lay.app, platform)) != 0:
            raise InstallError(t("installer.start_failed", path=lay.app))
    print(t("installer.done"))


def _remove_later(app: Path) -> None:
    """En Windows no se puede borrar la carpeta del Python que está corriendo: la borra un
    `cmd` aparte cuando este proceso ya terminó. Va como una sola línea (con `/s`, `cmd` le
    saca solo las comillas de afuera), así una ruta con espacios llega entera a `rmdir`. Corre
    parado en la carpeta de arriba: Windows no borra la carpeta en la que está un proceso."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(
        f'cmd.exe /d /s /c "ping -n 3 127.0.0.1 >nul & rmdir /s /q "{app}""',
        cwd=app.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def uninstall(
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    stopper: Stopper = stop,
) -> None:
    env = os.environ if env is None else env
    lay = layout(platform, Path.home() if home is None else home, env)
    settings = Settings()
    _close_running(env, stopper)
    remove_shortcuts(lay)
    if lay.app.exists():
        running_inside = Path(sys.prefix).resolve().is_relative_to(lay.app.resolve())
        if platform == "win32" and running_inside:
            _remove_later(lay.app)
        else:
            shutil.rmtree(lay.app)
    print(t("installer.uninstalled", path=settings.xport_data_dir))


def main(argv: Sequence[str] | None = None) -> int:
    safe_console()
    args_list = list(sys.argv[1:] if argv is None else argv)
    set_lang(parse_lang(argv_lang(args_list)) or system_lang())
    parser = argparse.ArgumentParser(prog="install", description=t("installer.help"))
    parser.add_argument("command", choices=("install", "uninstall"))
    parser.add_argument("--no-start", action="store_true", help=t("installer.opt_no_start"))
    parser.add_argument("--lang", choices=LANGS, help=t("cli.opt_lang"))
    args = parser.parse_args(args_list)
    try:
        if args.command == "install":
            install(start=not args.no_start)
        else:
            uninstall()
    except (InstallError, OSError) as e:
        print(t("installer.failed", error=e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
