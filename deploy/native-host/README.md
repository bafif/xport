# native-host — auto-arranque del backend de xport

Hace que **el navegador arranque y baje el FastAPI local solo**, vía la API de
*native messaging*. Así la extensión deja de depender de tener un `uvicorn` corriendo
a mano en WSL: abrís la extensión → se levanta el backend; cerrás el navegador → se baja.

**No reemplaza el servicio HTTP, lo supervisa.** La extensión sigue hablando HTTP
contra `http://localhost:8000` (igual que antes). El Compliance Gate, los `mappers/` y
el `storage/` quedan donde están, server-side. Esto solo automatiza el *arranque*.

```
navegador --connectNative--> host nativo --lanza--> uvicorn (FastAPI)
   (mantiene el puerto abierto = backend vivo durante la sesión)
   (cierra el puerto = EOF = el host baja uvicorn)
```

## Piezas

| archivo | qué es |
|---|---|
| `host.py` | supervisor (stdlib): arranca uvicorn, espera `/healthz`, bloquea en stdin, lo baja en EOF. Idempotente: si ya hay un server, no arranca otro ni lo mata. |
| `xport-host.sh` | wrapper que el navegador ejecuta: localiza `uv` y entra al venv (`uv run python host.py`). |
| `install-linux.sh` | instala el manifest para un navegador **dentro de** WSL/Linux (WSLg). |
| `install-windows.ps1` | instala el manifest + registro para Firefox/Chrome **en Windows** (bridge a WSL vía `wsl.exe`). |

## Elegí tu caso

### A) Firefox corre en Windows, backend en WSL (lo más común)

Desde **PowerShell en Windows**, una sola vez:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\bafif\xport\deploy\native-host\install-windows.ps1
# si tu distro no es la default o el repo está en otro lado:
#   -Distro Ubuntu  -RepoPath /home/bafif/xport
```

Escribe `%LOCALAPPDATA%\xport\xport-host.bat` + el manifest, y la clave de registro
`HKCU\Software\Mozilla\NativeMessagingHosts\com.xport.host`. El `.bat` hace
`wsl.exe -- bash xport-host.sh` y `wsl.exe` bridgea el canal nativo hasta WSL.

### B) Firefox/Chrome corre dentro de WSL/Linux (WSLg)

Desde **WSL**, una sola vez:

```bash
deploy/native-host/install-linux.sh            # Firefox
deploy/native-host/install-linux.sh --chrome --ext-id=<chrome-id>
```

## El id de la extensión

El manifest autoriza por id. El default es **`xport@local`** (ya fijado en
`extension/wxt.config.ts` → `browser_specific_settings.gecko.id`). Si lo cambiás,
pasá `--ext-id=` / `-ExtId`. En **Chrome** el id es el de 32 letras que aparece en
`chrome://extensions` al cargar la carpeta desempaquetada.

## Verificación

1. Asegurate de que NO haya un `uvicorn` corriendo a mano (para probar el auto-arranque).
2. Cargá/recargá la extensión (`about:debugging` en Firefox → "Cargar complemento temporal" → `dist/firefox-mv2/manifest.json`).
3. En la consola del background (`about:debugging` → "Inspeccionar") debería verse
   `native host conectado: backend local auto-arrancando`.
4. A los ~1–2 s, `http://localhost:8000/healthz` responde. Logs del host: `~/.local/state/xport/native-host.log`.
5. Cerrá Firefox → el host recibe EOF y baja uvicorn (salvo `XPORT_HOST_KEEP_ALIVE=1`).

## Desinstalar

```bash
deploy/native-host/install-linux.sh --uninstall
# Windows:
powershell -ExecutionPolicy Bypass -File ...\install-windows.ps1 -Uninstall
```

## Variables de entorno (opcionales)

| var | default | para qué |
|---|---|---|
| `XPORT_PORT` / `XPORT_HOST` | `8000` / `127.0.0.1` | dónde bindea uvicorn (debe matchear el `DEFAULT_BASE` de la extensión) |
| `XPORT_HOST_KEEP_ALIVE` | `0` | `1` = NO bajar uvicorn en EOF. Útil en **Chrome MV3** (su service worker es efímero y cerraría el puerto al dormirse). |
| `XPORT_HOST_LOG` | `~/.local/state/xport/native-host.log` | logfile del supervisor |

## Caveats

- **Chrome MV3**: el service worker se duerme y cerraría el puerto → el host bajaría
  uvicorn. Para ese navegador usá `XPORT_HOST_KEEP_ALIVE=1`. **Firefox MV2** (el target
  primario) tiene background persistente: anda sin tocar nada.
- **Ledger del gate compartido**: el host corre uvicorn desde la raíz del repo, así que
  usa el MISMO `data/audit/ledger.db` que un `uvicorn` manual. El Compliance Gate sigue
  siendo el único global. El auto-arranque no abre ningún camino que saltee el gate.
- **Una sola vez, fuera del navegador**: native messaging no se puede shippear solo por
  la web store — el manifest del host vive en el FS / registro. Por eso hay un instalador.
