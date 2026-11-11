#!/usr/bin/env python3
"""Arma el paquete instalable de xport para este sistema (punto 14 de docs/PLAN.md).

    bash deploy/extension-build.sh                # antes: la extensión armada
    uv run python deploy/bundle/build.py --smoke  # → dist/bundle/xport-<versión>-<sistema>-<arq>.*

El paquete trae su propio Python (el CPython portable de python-build-standalone, que baja uv)
con xport y las dependencias de `uv.lock` instaladas adentro, la extensión armada para Chrome, el
ícono, el lanzador `xport` y el instalador. Quien lo usa no necesita Python ni uv.

Se arma en el mismo sistema para el que es, porque las dependencias compiladas (pydantic-core,
uvloop) cambian de un sistema a otro. Linux sale en dos: `linux-x86_64`, armado en cualquier Linux
con glibc (Debian, Ubuntu, Fedora, Arch…), y `linux-x86_64-musl`, armado en Alpine, para Alpine.
Windows y macOS, en CI.

- Linux: `.tar.gz` con la carpeta `xport/`; se instala con `xport/install.sh`.
- Windows: `.zip` con la carpeta `xport/`; se instala con doble clic en `xport\\install.cmd`.
- macOS: `.tar.gz` con `xport.app`, que se arrastra a Aplicaciones (sin firmar: la primera vez se
  abre con clic derecho → Abrir).

`--smoke` prueba el paquete armado: arranca el servicio con el Python del paquete en un puerto
libre y una carpeta de datos temporal, mira que conteste y lo baja.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "3.12"
EXTENSION = ROOT / "extension" / "dist" / "chrome-mv3"
ICON_PNG = ROOT / "extension" / "public" / "icon" / "128.png"
MARK = "xport-bundle.json"  # el mismo nombre que `tweet_extractor.config.BUNDLE_MARK`

# Lo que xport no usa del CPython portable (la interfaz gráfica de Python, el instalador de pip,
# los tests de la biblioteca estándar, lo necesario para compilar extensiones).
TRIM_POSIX = (
    "bin/idle*",
    "bin/pip*",
    "bin/2to3*",
    "bin/pydoc*",
    "bin/python*-config",
    "include",
    "share",
    "lib/pkgconfig",
    "lib/itcl*",
    "lib/tcl*",
    "lib/tk*",
    "lib/thread*",
    "lib/libtcl*",
    "lib/libtk*",
    "lib/python3.*/config-*",
    "lib/python3.*/idlelib",
    "lib/python3.*/tkinter",
    "lib/python3.*/turtledemo",
    "lib/python3.*/ensurepip",
    "lib/python3.*/lib2to3",
    "lib/python3.*/test",
    "lib/python3.*/lib-dynload/_tkinter*",
    "lib/python3.*/site-packages/pip",
    "lib/python3.*/site-packages/pip-*",
)
TRIM_WINDOWS = (
    "Lib/idlelib",
    "Lib/tkinter",
    "Lib/turtledemo",
    "Lib/ensurepip",
    "Lib/lib2to3",
    "Lib/test",
    "Lib/site-packages/pip",
    "Lib/site-packages/pip-*",
    "DLLs/_tkinter.pyd",
    "DLLs/tcl*.dll",
    "DLLs/tk*.dll",
    "Scripts",
    "include",
    "libs",
    "tcl",
)


def system() -> tuple[str, str]:
    """(`linux` | `windows` | `macos`, `x86_64` | `arm64`), con `-musl` en un Linux con musl
    (Alpine): ahí uv baja el CPython para musl y las dependencias en sus ruedas musllinux."""
    name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if name == "linux" and any(Path("/lib").glob("ld-musl-*.so.1")):
        arch += "-musl"
    return name, arch


def run(*cmd: str | Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT, env=env)


def version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def python_bin(python_dir: Path, os_name: str) -> Path:
    return python_dir / ("python.exe" if os_name == "windows" else "bin/python3")


def fetch_python(work: Path) -> Path:
    """El CPython portable, recién bajado (o del caché de uv) a una carpeta propia."""
    target = work / "python-src"
    run("uv", "python", "install", PYTHON, "--install-dir", target, "--no-bin")
    found = [p for p in target.glob(f"cpython-{PYTHON}.*") if p.is_dir() and not p.is_symlink()]
    if len(found) != 1:
        sys.exit(f"esperaba un solo CPython {PYTHON} en {target}, hay {found}")
    return found[0]


def trim(python_dir: Path, os_name: str) -> None:
    for pattern in TRIM_WINDOWS if os_name == "windows" else TRIM_POSIX:
        for path in python_dir.glob(pattern):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    # uv marca sus Python como «administrados por uv» para que nadie les instale paquetes. Este
    # ya no es de uv: es el de xport.
    for mark in python_dir.glob("**/EXTERNALLY-MANAGED"):
        mark.unlink()


def install_xport(python: Path, work: Path) -> None:
    """Las dependencias exactas del lock (con sus hashes) y después xport, sin dependencias."""
    reqs = work / "requirements.txt"
    run(
        "uv",
        "export",
        "--quiet",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--format",
        "requirements.txt",
        "--output-file",
        reqs,
    )
    common = ("--python", python, "--compile-bytecode", "--link-mode", "copy", "--no-config")
    run("uv", "pip", "install", *common, "-r", reqs)
    wheels = work / "wheel"
    run("uv", "build", "--wheel", "--out-dir", wheels)
    (wheel,) = wheels.glob("*.whl")
    run("uv", "pip", "install", *common, "--no-deps", wheel)


def png_to_ico(png: bytes) -> bytes:
    """Un `.ico` con el PNG adentro (Windows lo acepta desde Vista)."""
    width, height = struct.unpack(">II", png[16:24])
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def png_to_icns(png: bytes) -> bytes:
    """Un `.icns` con el PNG de 128 px (`ic07`)."""
    body = b"ic07" + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def write(path: Path, text: str, *, executable: bool = False, crlf: bool = False) -> None:
    path.write_bytes(text.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))
    if executable:
        path.chmod(0o755)


def posix_script(module: str, *args: str) -> str:
    """Un script que corre `module` con el Python del paquete, desde donde esté la carpeta."""
    extra = "".join(f" {a}" for a in args)
    return (
        "#!/bin/sh\n"
        'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
        f'exec "$here/python/bin/python3" -I -m {module}{extra} "$@"\n'
    )


def cmd_script(module: str, *args: str, pause: bool = False, leave_dir: bool = False) -> str:
    """`leave_dir`: corre parado en %TEMP% (el desinstalador: Windows no borra la carpeta en la
    que está parada la consola). `%~dp0` sigue siendo la carpeta del script."""
    extra = "".join(f" {a}" for a in args)
    lines = ["@echo off"]
    if leave_dir:
        lines.append('cd /d "%TEMP%"')
    lines.append(f'"%~dp0python\\python.exe" -I -m {module}{extra} %*')
    if pause:
        lines.append("pause")
    return "\n".join(lines) + "\n"


def stage(work: Path, os_name: str, arch: str) -> Path:
    if not (EXTENSION / "manifest.json").is_file():
        sys.exit("falta la extensión armada: corré antes `bash deploy/extension-build.sh`")
    root = work / "xport"
    python_dir = root / "python"
    shutil.copytree(fetch_python(work), python_dir, symlinks=True)
    trim(python_dir, os_name)
    python = python_bin(python_dir, os_name)
    install_xport(python, work)
    shutil.copytree(EXTENSION, root / "extension")
    png = ICON_PNG.read_bytes()
    (root / "xport.png").write_bytes(png)
    full = subprocess.run(
        [str(python), "-I", "-c", "import platform; print(platform.python_version())"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    mark = {"name": "xport", "version": version(), "system": f"{os_name}-{arch}", "python": full}
    (root / MARK).write_text(json.dumps(mark, indent=2) + "\n", encoding="utf-8")
    if os_name == "windows":
        (root / "xport.ico").write_bytes(png_to_ico(png))
        write(root / "xport.cmd", cmd_script("tweet_extractor.launcher"), crlf=True)
        write(
            root / "install.cmd",
            cmd_script("tweet_extractor.installer", "install", pause=True),
            crlf=True,
        )
        write(
            root / "uninstall.cmd",
            cmd_script("tweet_extractor.installer", "uninstall", pause=True, leave_dir=True),
            crlf=True,
        )
    else:
        write(root / "xport", posix_script("tweet_extractor.launcher"), executable=True)
    if os_name == "linux":
        write(
            root / "install.sh",
            posix_script("tweet_extractor.installer", "install"),
            executable=True,
        )
        write(
            root / "uninstall.sh",
            posix_script("tweet_extractor.installer", "uninstall"),
            executable=True,
        )
    return root


INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>xport</string>
  <key>CFBundleDisplayName</key><string>xport</string>
  <key>CFBundleIdentifier</key><string>io.github.bafif.xport</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>xport</string>
  <key>CFBundleIconFile</key><string>xport.icns</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
"""

MAC_LAUNCHER = """#!/bin/sh
here=$(CDPATH= cd -- "$(dirname -- "$0")/../Resources/xport" && pwd -P)
exec "$here/python/bin/python3" -I -m tweet_extractor.launcher "$@"
"""


def macos_app(root: Path) -> Path:
    """`xport.app`: el paquete entero en `Contents/Resources/xport` y un lanzador que lo usa.
    `LSUIElement`: el lanzador abre la página y termina, sin ícono en el Dock."""
    app = root.parent / "xport.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    root.rename(contents / "Resources" / "xport")
    write(contents / "Info.plist", INFO_PLIST.format(version=version()))
    write(contents / "MacOS" / "xport", MAC_LAUNCHER, executable=True)
    (contents / "Resources" / "xport.icns").write_bytes(png_to_icns(ICON_PNG.read_bytes()))
    return app


def archive(top: Path, out: Path, os_name: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if os_name == "windows":
        path = out.with_name(out.name + ".zip")  # `with_suffix` cortaría en «0.2.1»
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for file in sorted(top.rglob("*")):
                z.write(file, file.relative_to(top.parent))
        return path
    path = out.with_name(out.name + ".tar.gz")
    with tarfile.open(path, "w:gz", compresslevel=9) as tar:
        tar.add(top, arcname=top.name)
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def smoke(root: Path, os_name: str) -> None:
    """El servicio arranca con el Python del paquete, contesta y baja. Sin navegador y con una
    carpeta de datos que se tira: no toca los datos de quien arma."""
    python = python_bin(root / "python", os_name)
    with tempfile.TemporaryDirectory() as data:
        env = dict(
            os.environ,
            XPORT_DATA_DIR=data,
            XPORT_PORT=str(_free_port()),
            XPORT_PEER_PORTS="[]",
            XPORT_BROWSER="false",
            XPORT_NOTIFY="false",
            XPORT_LANG="es",
        )
        launcher = (python, "-I", "-m", "tweet_extractor.launcher")
        # Lo que falta para capturar sin ventana (en CI, cage y Chrome): se muestra, no corta.
        subprocess.run([str(c) for c in (*launcher, "check")], env=env, check=False)
        try:
            run(*launcher, "start", env=env)
            run(*launcher, "status", env=env)
            run(*launcher, "stop", env=env)
        except subprocess.CalledProcessError:
            log = Path(data) / "service.log"
            if log.is_file():
                print(log.read_text(encoding="utf-8", errors="replace"))
            raise
    print("prueba de humo: el servicio del paquete arrancó, contestó y se cerró")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "bundle")
    parser.add_argument("--smoke", action="store_true", help="probar el paquete armado")
    args = parser.parse_args()
    os_name, arch = system()
    out: Path = args.out.resolve()
    with tempfile.TemporaryDirectory(prefix="xport-bundle-") as tmp:
        root = stage(Path(tmp), os_name, arch)
        if args.smoke:
            smoke(root, os_name)
        top = macos_app(root) if os_name == "macos" else root
        path = archive(top, out / f"xport-{version()}-{os_name}-{arch}", os_name)
    size = path.stat().st_size / 1024 / 1024
    print(f"listo: {path} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
