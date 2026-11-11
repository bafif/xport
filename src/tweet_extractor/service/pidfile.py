"""El número de proceso del servicio que corre, en `<carpeta de datos>/service.pid`.

Lo escribe el servicio al arrancar, después de comprobar que no hay otro xport corriendo, y lo
borra al bajar. Lo lee `xport stop` (`tweet_extractor/launcher.py`) para bajarlo, lo haya
arrancado el lanzador, el native host o una persona a mano.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

PID_FILE = "service.pid"


def pid_path(data_dir: Path) -> Path:
    return data_dir / PID_FILE


def write_pid(data_dir: Path, pid: int | None = None) -> Path:
    path = pid_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid() if pid is None else pid}\n", encoding="ascii")
    return path


def read_pid(data_dir: Path) -> int | None:
    try:
        return int(pid_path(data_dir).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def clear_pid(data_dir: Path, pid: int | None = None) -> None:
    """Lo borra si es el de `pid` (por defecto, este proceso): así un servicio que arrancó
    después no pierde el suyo."""
    if read_pid(data_dir) == (os.getpid() if pid is None else pid):
        with contextlib.suppress(OSError):
            pid_path(data_dir).unlink()
