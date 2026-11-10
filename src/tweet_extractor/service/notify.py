"""Notificaciones del sistema cuando una captura de la cola termina o se corta (punto 9 de
docs/PLAN.md). Las manda el servicio y no la extensión, que va a correr en un navegador sin
ventana. Son de mejor esfuerzo: si el sistema no tiene cómo mostrarlas, queda en el log y
la captura sigue igual. Al hacer clic, la de una captura terminada abre su CSV (en Linux con
`notify-send --action`, en Windows con un toast; en macOS `osascript` no tiene clic).

Ninguno de los tres está probado a mano todavía: Linux se escribió para notify-send 0.8 (el
`--action` existe desde la 0.7.10), Windows y macOS según su documentación.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from tweet_extractor.i18n import t

log = logging.getLogger(__name__)

Send = Callable[[str, str, Path | None], Coroutine[Any, Any, None]]

# Toast de Windows sin módulos extra. Los textos van por variables de entorno, así nada de lo
# que se muestra se interpreta como código. El AppUserModelID es el de PowerShell, que
# Windows ya conoce: sin eso, un toast de un programa sin acceso directo no se muestra.
_PS_TOAST = "\n".join(
    (
        "$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime]",
        "$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime]",
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
        '$xml.LoadXml(\'<toast><visual><binding template="ToastGeneric">'
        "<text></text><text></text></binding></visual></toast>')",
        "$texts = $xml.GetElementsByTagName('text')",
        "$null = $texts.Item(0).AppendChild($xml.CreateTextNode($env:XPORT_TITLE))",
        "$null = $texts.Item(1).AppendChild($xml.CreateTextNode($env:XPORT_BODY))",
        "if ($env:XPORT_OPEN) {",
        "  $toast = $xml.SelectSingleNode('/toast')",
        "  $toast.SetAttribute('activationType', 'protocol')",
        "  $toast.SetAttribute('launch', $env:XPORT_OPEN)",
        "}",
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'",
        "$manager = [Windows.UI.Notifications.ToastNotificationManager]",
        "$notifier = $manager::CreateToastNotifier($app)",
        "$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($xml))",
    )
)

_OSA = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)


async def _run(*args: str, env: dict[str, str] | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    out, _ = await proc.communicate()
    if proc.returncode:
        raise OSError(f"{Path(args[0]).name} terminó con {proc.returncode}")
    return out


async def _linux(title: str, body: str, open_path: Path | None) -> None:
    exe = shutil.which("notify-send")
    if exe is None:
        raise FileNotFoundError("notify-send")
    if open_path is None:
        await _run(exe, "--app-name=xport", title, body)
        return
    # `--wait` deja el proceso vivo hasta que la notificación se cierra o vence, y escribe
    # la acción elegida. Corre en una tarea aparte (ver `Notifier`), sin frenar a nadie.
    chosen = await _run(
        exe, "--app-name=xport", f"--action=open={t('notify.open_csv')}", "--wait", title, body
    )
    opener = shutil.which("xdg-open")
    if chosen.strip() == b"open" and opener is not None:
        await _run(opener, str(open_path))


async def _windows(title: str, body: str, open_path: Path | None) -> None:
    env = dict(os.environ, XPORT_TITLE=title, XPORT_BODY=body)
    env["XPORT_OPEN"] = open_path.as_uri() if open_path else ""
    await _run("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_TOAST, env=env)


async def system_notify(title: str, body: str, open_path: Path | None = None) -> None:
    """Muestra una notificación del sistema. No tira: si no se puede, lo deja en el log."""
    try:
        if sys.platform == "win32":
            await _windows(title, body, open_path)
        elif sys.platform == "darwin":
            await _run("osascript", *(a for line in _OSA for a in ("-e", line)), title, body)
        else:
            await _linux(title, body, open_path)
    except OSError as e:
        log.info("no pude mostrar la notificación (%s): %s. %s", e, title, body)


async def _silent(title: str, body: str, open_path: Path | None = None) -> None:
    log.info("%s. %s", title, body)


class Notifier:
    """Manda cada notificación en una tarea aparte, así un pedido no espera a que la
    persona la cierre. `shutdown` corta las que sigan abiertas al bajar el servicio."""

    def __init__(self, send: Send | None = None, *, enabled: bool = True) -> None:
        self._send: Send = send or (system_notify if enabled else _silent)
        self._tasks: set[asyncio.Task[None]] = set()

    def post(self, title: str, body: str, open_path: Path | None = None) -> None:
        task = asyncio.create_task(self._send(title, body, open_path))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Espera a que salgan las notificaciones pendientes (para los tests)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.drain()
