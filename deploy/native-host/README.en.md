# native-host — backend auto-start for xport

[Español](README.md)

Makes **the browser start and stop the local FastAPI on its own**, via the *native messaging*
API. That way the extension no longer depends on having a `uvicorn` running by hand in WSL:
open the extension → the backend comes up; close the browser → it goes down.

**It doesn't replace the HTTP service, it supervises it.** The extension still talks HTTP
to `http://localhost:8000` (same as before). The Compliance Gate, the `mappers/` and the
`storage/` stay where they are, server-side. This only automates the *startup*.

```
browser --connectNative--> native host --launches--> uvicorn (FastAPI)
   (as long as the port stays open, the backend stays alive)
   (port closes = EOF = the host shuts uvicorn down)
```

## Pieces

| file | what it is |
|---|---|
| `host.py` | supervisor (stdlib): starts uvicorn, waits for `/healthz`, blocks on stdin, shuts it down on EOF. Idempotent: if a server is already running, it doesn't start another one or kill it. |
| `xport-host.sh` | wrapper the browser runs: locates `uv` and enters the venv (`uv run python host.py`). |
| `install-linux.sh` | installs the manifest for a browser **inside** WSL/Linux (WSLg). |
| `install-windows.ps1` | installs the manifest + registry entry for Firefox/Chrome **on Windows** (bridge to WSL via `wsl.exe`). |

## Pick your case

### A) Firefox runs on Windows, backend in WSL (the most common)

From **PowerShell on Windows**, just once:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\<usuario>\xport\deploy\native-host\install-windows.ps1 -RepoPath /home/<usuario>/xport
# if your distro isn't the default one, add: -Distro Ubuntu
```

It writes `%LOCALAPPDATA%\xport\xport-host.bat` + the manifest, and the registry key
`HKCU\Software\Mozilla\NativeMessagingHosts\com.xport.host`. The `.bat` runs
`wsl.exe -- bash xport-host.sh` and `wsl.exe` bridges the native channel into WSL.

### B) Firefox/Chrome runs inside WSL/Linux (WSLg)

From **WSL**, just once:

```bash
deploy/native-host/install-linux.sh            # Firefox
deploy/native-host/install-linux.sh --chrome   # Chrome, Chrome Beta/Dev and Chromium
```

## The extension id

The manifest authorizes by id, and both ids are pinned in `extension/wxt.config.ts`: on
**Firefox**, **`xport@local`** (`browser_specific_settings.gecko.id`); on **Chrome**,
**`aiildgjidjmeilmjinaililpijenmodi`**, which is derived from the manifest's public key (`key`), so it doesn't change
depending on the folder it's loaded from. The installers use those ids on their own; if you change them,
pass `--ext-id=` / `-ExtId`.

## Verification

1. Make sure there's NO `uvicorn` running by hand (so you're actually testing the auto-start).
2. Load/reload the extension (`about:debugging` in Firefox → "Load Temporary Add-on" → `dist/firefox-mv2/manifest.json`).
3. In the background console (`about:debugging` → "Inspect") you should see
   `native host conectado: backend local auto-arrancando`.
4. After ~1–2 s, `http://localhost:8000/healthz` responds. Host logs: `~/.local/state/xport/native-host.log`.
5. Close Firefox → the host gets EOF and shuts uvicorn down (unless `XPORT_HOST_KEEP_ALIVE=1`).

## Diagnostics (doctor)

Two automated checks, one on each side of the WSL boundary:

- **WSL** — that the backend can start (+ a real self-test of the supervisor if `:8000` is free):
  ```bash
  deploy/native-host/doctor.sh            # --no-live to skip the self-test
  ```
- **Windows** — registry + manifest + `.bat`→WSL bridge + reachability from Windows:
  ```powershell
  powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\<usuario>\xport\deploy\native-host\doctor.ps1
  #   -Distro <d>   if it isn't the default one     -Chrome   for the Chrome manifest
  ```
  (You can also run the WSL one from PowerShell: `wsl bash /home/<usuario>/xport/deploy/native-host/doctor.sh`.)

## Uninstall

```bash
deploy/native-host/install-linux.sh --uninstall
# Windows:
powershell -ExecutionPolicy Bypass -File ...\install-windows.ps1 -Uninstall
```

## Environment variables (optional)

| var | default | what for |
|---|---|---|
| `XPORT_PORT` / `XPORT_HOST` | `8000` / `127.0.0.1` | where uvicorn binds (must match the extension's `DEFAULT_BASE`) |
| `XPORT_HOST_KEEP_ALIVE` | `0` | `1` = do NOT shut uvicorn down on EOF. With **Chrome MV3** it turns on by itself (its service worker is ephemeral and would close the port when it goes to sleep). |
| `XPORT_HOST_LOG` | `~/.local/state/xport/native-host.log` | supervisor logfile |

## Caveats

- **WSL in NAT mode + `localhost` (IPv4 vs IPv6)**: WSL2 forwarding forwards IPv4
  `127.0.0.1`; on Windows, `localhost` resolves to `::1` (IPv6) first. The browser falls
  back to IPv4 (the extension works with `localhost:8000`), but strict tools like
  PowerShell's `Invoke-WebRequest` don't — that's why `doctor.ps1` uses `127.0.0.1`. If
  Windows **can't** reach the backend even via `127.0.0.1` (but it does respond inside WSL),
  forwarding is broken: set `localhostForwarding=true` under `[wsl2]` in `%UserProfile%\.wslconfig`
  and run `wsl --shutdown`, or switch to `networkingMode=mirrored`. (The WSL `172.x` IP works
  as a fallback but **changes on every restart**, which is why it isn't hardcoded.)
- **Chrome MV3**: the service worker goes to sleep and would close the port → the host would shut
  uvicorn down. The host recognizes Chrome by the `chrome-extension://…` origin it receives as an
  argument, and in that case keeps uvicorn alive, as with `XPORT_HOST_KEEP_ALIVE=1`; it goes down
  on restart or by hand. **Firefox MV2** has a persistent background: it works without touching anything.
- **Shared gate ledger**: the host, a manual `uvicorn` and the CLI use the same
  per-user data folder (`~/.local/share/xport` on Linux; `XPORT_DATA_DIR` changes it),
  no matter where they're started from, so there's a single ledger. If another xport is
  already on 8000 or 8080, the service doesn't start and the host log says why. The
  auto-start doesn't open any path that bypasses the gate.
- **Once, outside the browser**: native messaging can't be shipped through the web store
  alone — the host manifest lives on the filesystem / registry. That's why there's an installer.