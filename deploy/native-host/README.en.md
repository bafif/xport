# The native messaging host: the browser starts the service

[Español](README.md) · [Back to the README](../../README.en.md)

For running xport from the source. With the installer you do not need it: the «xport» shortcut starts the service.

With this host, **the browser starts the local service when the extension opens and stops it when the browser
closes**, through *native messaging*. It does not replace the service: the extension still talks to it over HTTP at
`http://localhost:8000`, and everything captured goes through the same cap ledger. An extension cannot install the
host by itself, because its manifest lives on the system (or in the Windows registry): that is why it is installed
once, separately.

```
browser --connectNative--> host.py --starts--> uvicorn (the service)
   while the channel stays open, the service stays alive
   the browser closes the channel (EOF) and the host stops the service
```

## The pieces

| File | What it does |
|---|---|
| `host.py` | starts uvicorn, waits for `/healthz`, listens on the channel and stops the service when it closes. If a service is already running, it neither starts another one nor stops it. |
| `xport-host.sh` | what the browser runs: finds `uv` and runs `host.py` in the repository's environment |
| `install-linux.sh` | installs the host for a Linux browser (also inside WSL, with WSLg) |
| `install-windows.ps1` | installs the host for a Windows browser, with the repository inside WSL |
| `doctor.sh`, `doctor.ps1` | check the installation, on the Linux or WSL side and on the Windows side |

## Installing

### Browser on Linux

```bash
deploy/native-host/install-linux.sh            # Firefox
deploy/native-host/install-linux.sh --chrome   # Chrome, Chrome Beta, Chrome Dev and Chromium
```

### Browser on Windows, repository in WSL

This path has not been tried end to end. From PowerShell on Windows, once:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\<user>\xport\deploy\native-host\install-windows.ps1 -RepoPath /home/<user>/xport
# -Distro <name> if it is not the default distribution; -Chrome for Chrome
```

It leaves `xport-host.bat` and the manifest in `%LOCALAPPDATA%\xport` and registers the host under
`HKCU\Software\Mozilla\NativeMessagingHosts\com.xport.host` (with `-Chrome`, under Chrome's key). The `.bat` runs
`wsl.exe -- bash xport-host.sh`, and `wsl.exe` carries the channel into WSL.

## The extension id

The host's manifest authorizes the extension by its id, pinned in `extension/wxt.config.ts`: `xport@local` on Firefox
(`browser_specific_settings.gecko.id`) and `aiildgjidjmeilmjinaililpijenmodi` on Chrome, which comes from the
manifest's public key (`key`) and does not change with the folder it is loaded from. The installers already use those
ids; if you change one, pass it with `--ext-id=<id>` or `-ExtId <id>`.

## Checking that it works

1. Make sure no other xport service is running.
2. Load or reload the extension: on Firefox, `about:debugging` → «This Firefox» → «Load Temporary Add-on…» →
   `extension/dist/firefox-mv2/manifest.json`.
3. In the extension's console (`about:debugging` → «Inspect») you see
   `native host conectado: backend local auto-arrancando`.
4. Within a second or two, `http://localhost:8000/healthz` answers. The host's log is at
   `~/.local/state/xport/native-host.log`.
5. When you close Firefox, the host stops the service (unless `XPORT_HOST_KEEP_ALIVE=1`).

## Diagnosis

- **Linux or WSL:** `deploy/native-host/doctor.sh` checks that the service can start and, if port 8000 is free, tries
  the host for real, without a browser (`--no-live` skips that test). From Windows, run it with
  `wsl bash /home/<user>/xport/deploy/native-host/doctor.sh`.
- **Windows:** `doctor.ps1` checks the registry, the manifest, the `.bat` and that Windows reaches the service:

  ```powershell
  powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\<user>\xport\deploy\native-host\doctor.ps1
  # -Distro <name> if it is not the default distribution; -Chrome for Chrome's host
  ```

## Uninstalling

```bash
deploy/native-host/install-linux.sh --uninstall            # with --chrome, Chrome's
```

```powershell
powershell -ExecutionPolicy Bypass -File ...\install-windows.ps1 -Uninstall
```

## Environment variables (optional)

| Variable | Default | What for |
|---|---|---|
| `XPORT_PORT`, `XPORT_HOST` | `8000`, `127.0.0.1` | where the service listens; it has to be the address the extension uses |
| `XPORT_HOST_KEEP_ALIVE` | `0` | with `1`, the host does not stop the service when the channel closes; with Chrome it turns on by itself |
| `XPORT_HOST_LOG` | `~/.local/state/xport/native-host.log` | the host's log |

## Things to keep in mind

- **Chrome:** the service worker of an MV3 extension goes to sleep and would close the channel. The host recognizes
  Chrome by the `chrome-extension://…` origin it receives and keeps the service alive, as with
  `XPORT_HOST_KEEP_ALIVE=1`; it goes down on restart or by hand. Firefox (MV2) has a persistent background and needs
  nothing.
- **A single cap ledger:** the host, a `uvicorn` started by hand and the CLI use the same data folder, so there is a
  single ledger. If another xport is already on port 8000 or 8080, the service does not start and the host's log says
  why.
- **WSL in NAT mode:** WSL forwards `127.0.0.1`, but on Windows `localhost` resolves to `::1` first. The browser falls
  back to IPv4 by itself; `Invoke-WebRequest` does not, which is why `doctor.ps1` uses `127.0.0.1`. If Windows cannot
  reach the service even through `127.0.0.1` while it answers inside WSL, forwarding is broken: set
  `localhostForwarding=true` under `[wsl2]` in `%UserProfile%\.wslconfig` and run `wsl --shutdown`, or switch to
  `networkingMode=mirrored`.
