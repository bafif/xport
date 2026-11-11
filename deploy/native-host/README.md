# El host de native messaging: el navegador arranca el servicio

[English](README.en.md) · [Volver al README](../../README.md)

Para correr xport desde el código. Con el instalador no hace falta: el acceso directo «xport» arranca el servicio.

Con este host, **el navegador arranca el servicio local cuando se abre la extensión y lo baja cuando el navegador se
cierra**, por *native messaging*. No reemplaza al servicio: la extensión le sigue hablando por HTTP en
`http://localhost:8000`, y todo lo que se captura pasa por el mismo registro del tope. Una extensión no puede instalar
el host por su cuenta, porque su manifiesto va en el sistema (o en el registro de Windows): por eso se instala una vez,
aparte.

```
navegador --connectNative--> host.py --arranca--> uvicorn (el servicio)
   mientras el canal sigue abierto, el servicio sigue vivo
   el navegador cierra el canal (EOF) y el host baja el servicio
```

## Las piezas

| Archivo | Qué hace |
|---|---|
| `host.py` | arranca uvicorn, espera `/healthz`, escucha el canal y baja el servicio cuando se cierra. Si ya hay un servicio corriendo, no arranca otro ni lo cierra. |
| `xport-host.sh` | lo que ejecuta el navegador: busca `uv` y corre `host.py` en el entorno del repo |
| `install-linux.sh` | instala el host para un navegador de Linux (también dentro de WSL, con WSLg) |
| `install-windows.ps1` | instala el host para un navegador de Windows, con el repo dentro de WSL |
| `doctor.sh`, `doctor.ps1` | revisan la instalación, del lado de Linux o WSL y del lado de Windows |

## Instalar

### Navegador en Linux

```bash
deploy/native-host/install-linux.sh            # Firefox
deploy/native-host/install-linux.sh --chrome   # Chrome, Chrome Beta, Chrome Dev y Chromium
```

### Navegador en Windows, repo en WSL

Este camino no se probó de punta a punta. Desde PowerShell en Windows, una vez:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\<usuario>\xport\deploy\native-host\install-windows.ps1 -RepoPath /home/<usuario>/xport
# -Distro <nombre> si no es la distribución por defecto; -Chrome para Chrome
```

Deja `xport-host.bat` y el manifiesto en `%LOCALAPPDATA%\xport` y registra el host en
`HKCU\Software\Mozilla\NativeMessagingHosts\com.xport.host` (con `-Chrome`, en la clave de Chrome). El `.bat` corre
`wsl.exe -- bash xport-host.sh`, y `wsl.exe` lleva el canal hasta WSL.

## El id de la extensión

El manifiesto del host autoriza a la extensión por su id, fijado en `extension/wxt.config.ts`: `xport@local` en Firefox
(`browser_specific_settings.gecko.id`) y `aiildgjidjmeilmjinaililpijenmodi` en Chrome, que sale de la clave pública del
manifiesto (`key`) y no cambia según la carpeta desde la que se carga. Los instaladores ya usan esos ids; si cambiás
alguno, pasalo con `--ext-id=<id>` o `-ExtId <id>`.

## Comprobar que anda

1. Que no haya otro servicio de xport corriendo.
2. Cargá o recargá la extensión: en Firefox, `about:debugging` → «Este Firefox» → «Cargar complemento temporal…» →
   `extension/dist/firefox-mv2/manifest.json`.
3. En la consola de la extensión (`about:debugging` → «Inspeccionar») aparece
   `native host conectado: backend local auto-arrancando`.
4. En uno o dos segundos, `http://localhost:8000/healthz` responde. El log del host está en
   `~/.local/state/xport/native-host.log`.
5. Al cerrar Firefox, el host baja el servicio (salvo con `XPORT_HOST_KEEP_ALIVE=1`).

## Diagnóstico

- **Linux o WSL:** `deploy/native-host/doctor.sh` revisa que el servicio pueda arrancar y, si el puerto 8000 está
  libre, prueba el host de verdad, sin navegador (`--no-live` saltea esa prueba). Desde Windows se corre con
  `wsl bash /home/<usuario>/xport/deploy/native-host/doctor.sh`.
- **Windows:** `doctor.ps1` revisa el registro, el manifiesto, el `.bat` y que Windows llegue al servicio:

  ```powershell
  powershell -ExecutionPolicy Bypass -File \\wsl.localhost\<distro>\home\<usuario>\xport\deploy\native-host\doctor.ps1
  # -Distro <nombre> si no es la distribución por defecto; -Chrome para el host de Chrome
  ```

## Desinstalar

```bash
deploy/native-host/install-linux.sh --uninstall            # con --chrome, el de Chrome
```

```powershell
powershell -ExecutionPolicy Bypass -File ...\install-windows.ps1 -Uninstall
```

## Variables de entorno (opcionales)

| Variable | Por defecto | Para qué |
|---|---|---|
| `XPORT_PORT`, `XPORT_HOST` | `8000`, `127.0.0.1` | dónde escucha el servicio; tiene que ser la dirección que usa la extensión |
| `XPORT_HOST_KEEP_ALIVE` | `0` | con `1`, el host no baja el servicio cuando se cierra el canal; con Chrome se prende solo |
| `XPORT_HOST_LOG` | `~/.local/state/xport/native-host.log` | el log del host |

## A tener en cuenta

- **Chrome:** el service worker de una extensión MV3 se duerme y cerraría el canal. El host reconoce a Chrome por el
  origen `chrome-extension://…` que recibe y deja el servicio vivo, como con `XPORT_HOST_KEEP_ALIVE=1`; se baja al
  reiniciar o a mano. Firefox (MV2) tiene un background persistente y no necesita nada.
- **Un solo registro del tope:** el host, un `uvicorn` levantado a mano y la CLI usan la misma carpeta de datos, así
  que el registro es uno solo. Si ya hay otro xport en el puerto 8000 o el 8080, el servicio no arranca y el log del
  host dice por qué.
- **WSL en modo NAT:** WSL reenvía `127.0.0.1`, pero en Windows `localhost` resuelve primero a `::1`. El navegador
  vuelve solo a IPv4; `Invoke-WebRequest` no, y por eso `doctor.ps1` usa `127.0.0.1`. Si Windows no llega al servicio
  ni por `127.0.0.1` y dentro de WSL sí responde, el reenvío está roto: poné `localhostForwarding=true` bajo `[wsl2]`
  en `%UserProfile%\.wslconfig` y corré `wsl --shutdown`, o pasá a `networkingMode=mirrored`.
