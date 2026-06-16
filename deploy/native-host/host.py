#!/usr/bin/env python3
"""Native-messaging host de xport: SUPERVISA el FastAPI local (uvicorn) para que el
navegador lo arranque y lo baje solo. Así la extensión deja de depender de que haya
un `uvicorn` corriendo a mano en WSL: abrir el navegador levanta el backend.

NO reemplaza el servicio HTTP: lo lanza. La extensión sigue hablando HTTP contra
`http://localhost:8000` (vía el reenvío de localhost de WSL2) exactamente como antes.

Protocolo (importante):
- stdin/stdout es el canal de native messaging (JSON con prefijo de longitud). Este
  host NO escribe NADA en stdout (la extensión sondea /healthz para saber si está
  listo), y redirige stdout/stderr del hijo uvicorn a un logfile: si uvicorn
  escribiera al pipe nativo, corrompería el protocolo y el navegador cerraría.
- De stdin solo nos importa el EOF: cuando el navegador cierra el puerto nativo
  (se descarga la extensión / se cierra el navegador), `read()` devuelve b'' y
  bajamos el server. No parseamos frames: cualquier byte entrante se descarta.

Idempotente: si /healthz ya responde (server levantado a mano o por otra instancia),
NO arranca un segundo uvicorn y NO lo mata al salir (solo baja lo que arrancó él).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOST = os.environ.get("XPORT_HOST", "127.0.0.1")
PORT = int(os.environ.get("XPORT_PORT", "8000"))
APP = os.environ.get("XPORT_APP", "tweet_extractor.service.app:app")
HEALTH_URL = f"http://{HOST}:{PORT}/healthz"
# Chrome MV3: el service worker es efímero y al dormirse cerraría el puerto → EOF.
# Con XPORT_HOST_KEEP_ALIVE=1 el host NO mata uvicorn en EOF (queda vivo entre ciclos
# del SW); el server se baja a mano o al reiniciar. Default 0: ciclo de vida atado al
# puerto (ideal para Firefox MV2, cuyo background persiste toda la sesión).
KEEP_ALIVE = os.environ.get("XPORT_HOST_KEEP_ALIVE") == "1"
READY_TIMEOUT_S = float(os.environ.get("XPORT_HOST_READY_TIMEOUT", "30"))
LOG_PATH = Path(
    os.environ.get("XPORT_HOST_LOG", str(Path.home() / ".local/state/xport/native-host.log"))
)


def log(msg: str) -> None:
    """Log a archivo (NUNCA a stdout: stdout es el canal nativo)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass  # un log roto no debe tumbar al supervisor


def healthy(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:  # noqa: S310 (localhost)
            return 200 <= r.status < 300
    except Exception:
        return False


def start_server() -> subprocess.Popen[bytes]:
    """Arranca uvicorn con el MISMO intérprete del venv (entramos por `uv run python`,
    así uvicorn ya es importable). stdout/stderr → logfile, jamás el pipe nativo.
    `start_new_session=True` le da su propio grupo de procesos → kill limpio del árbol."""
    logfile = LOG_PATH.open("a", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", APP, "--host", HOST, "--port", str(PORT)],
        stdin=subprocess.DEVNULL,
        stdout=logfile,
        stderr=logfile,
        start_new_session=True,
    )


def main() -> int:
    log(f"host arranca (pid {os.getpid()}) → target {HOST}:{PORT}")

    proc: subprocess.Popen[bytes] | None = None
    if healthy():
        log("uvicorn ya estaba corriendo: superviso pero NO lo voy a bajar (no lo arranqué yo)")
    else:
        proc = start_server()
        log(f"uvicorn lanzado (pid {proc.pid}); esperando /healthz")
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if healthy():
                break
            if proc.poll() is not None:
                log(f"uvicorn murió al arrancar (rc={proc.returncode}); ver {LOG_PATH}")
                return 1
            time.sleep(0.2)
        log("uvicorn listo" if healthy() else "uvicorn no respondió /healthz a tiempo (sigo igual)")

    def shutdown() -> None:
        if proc is None or KEEP_ALIVE:
            log("salida: dejo uvicorn corriendo (keep-alive o no lo arranqué yo)")
            return
        if proc.poll() is not None:
            return
        log(f"salida: bajando uvicorn (pid {proc.pid})")
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def on_signal(signum: int, _frame: object) -> None:
        log(f"señal {signum}")
        shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Bloquea hasta que el navegador cierre el puerto nativo (EOF). read(1) duerme el
    # proceso casi todo el tiempo. No interpretamos los bytes: solo esperamos el cierre.
    try:
        while sys.stdin.buffer.read(1):
            pass
    except Exception as e:  # pragma: no cover - I/O del pipe
        log(f"stdin error: {e!r}")
    log("stdin EOF (el navegador cerró el puerto)")
    shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
