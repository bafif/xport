# Instalar xport

[English](INSTALL.en.md) · [Volver al README](../README.md)

## Qué hace falta

- **Un navegador:** Google Chrome, Chromium o Microsoft Edge. Firefox sirve solo con la extensión cargada a mano.
- **En Linux, [cage](https://github.com/cage-kiosk/cage):** la pantalla invisible donde corre el Chrome de xport. X no
  atiende a Chrome en modo `--headless`, así que xport lo abre en una pantalla que nadie ve. Se instala con
  `sudo apt install cage` (Debian, Ubuntu), `sudo pacman -S cage` (Arch) o `sudo dnf install cage` (Fedora). Sin cage,
  xport anda igual, pero la captura la hace la extensión en tu navegador, con la pestaña a la vista.
- **Una cuenta de X**, mejor aparte de la tuya: X puede limitar las cuentas que bajan muchos tweets.

El instalador trae su propio Python: no hace falta instalar Python, uv ni Node.

## Con el instalador

Los paquetes están en los [releases de GitHub](https://github.com/bafif/xport/releases/latest), uno por sistema. Si el
de tu sistema no está, [armalo](#armar-el-instalador).

### Linux

```sh
tar -xzf xport-<versión>-linux-x86_64.tar.gz
./xport/install.sh
```

Copia xport a `~/.local/lib/xport`, lo agrega al menú de aplicaciones y como el comando `xport` en `~/.local/bin`,
avisa si falta algo (con el comando que lo instala en tu distribución) y abre la página. No pide contraseña.

### Windows

1. Descomprimí `xport-<versión>-windows-x86_64.zip`.
2. Hacé doble clic en `xport\install.cmd`. Como el paquete no está firmado, Windows puede avisar: «Más información» →
   «Ejecutar de todos modos».
3. Queda en `%LOCALAPPDATA%\Programs\xport`, con el acceso directo «xport» en el menú Inicio y en el escritorio, y se
   abre la página.
4. [Cargá la extensión](#la-extensión-en-tu-navegador) en Chrome o Edge.

### macOS

1. Abrí `xport-<versión>-macos-arm64.tar.gz` (el Finder lo descomprime) y arrastrá `xport.app` a Aplicaciones. El
   paquete `arm64` es para las Mac con Apple Silicon; para una Mac con Intel, [armalo](#armar-el-instalador) en esa Mac.
2. Abrí xport. Como no está firmado, la primera vez macOS lo frena: en macOS 15 o más nuevo, andá a Configuración del
   Sistema → Privacidad y seguridad y apretá «Abrir igualmente»; en versiones anteriores, clic derecho sobre
   `xport.app` → «Abrir». Un `xport.app` armado en la misma Mac no pasa por esto.
3. [Cargá la extensión](#la-extensión-en-tu-navegador) en Chrome.

### La primera vez

La página de xport ofrece «Iniciar sesión en X»: abre una ventana de Chrome con el perfil de xport para que entres con
tu cuenta de X. Se hace una sola vez; si X cierra la sesión más adelante, la ventana vuelve a aparecer sola.

## La extensión en tu navegador

En Linux con cage, xport usa su propio Chrome y no hace falta cargar nada. En Windows, en macOS y en Linux sin cage, la
captura la hace la extensión de xport en tu navegador, que se carga una vez:

1. Abrí `chrome://extensions` (en Edge, `edge://extensions`) y prendé el «Modo de desarrollador».
2. «Cargar descomprimida» y elegí la carpeta `extension` de xport:

   | Sistema | Carpeta |
   |---|---|
   | Linux | `~/.local/lib/xport/extension` |
   | Windows | `%LOCALAPPDATA%\Programs\xport\extension` |
   | macOS | `/Applications/xport.app/Contents/Resources/xport/extension` |

   En macOS, en el cuadro para elegir la carpeta, apretá ⌘⇧G y pegá la ruta.

`xport check` (o `xport.cmd check` en Windows) muestra la carpeta exacta. Mientras captura, la pestaña de X que abre la
extensión tiene que quedar a la vista: si la tapás, la captura se pone en pausa y sigue cuando volvés.

**En Firefox:** bajá `xport-<versión>-firefox.zip` del mismo release, abrí `about:debugging` → «Este Firefox» →
«Cargar complemento temporal…» y elegí el zip. Firefox lo borra al cerrarse: hay que cargarlo cada vez.

## Abrir, cerrar y revisar

El acceso directo «xport» arranca el servicio si no está corriendo y abre la página. Desde una terminal:

| Comando | Qué hace |
|---|---|
| `xport` | arranca el servicio y abre la página |
| `xport start` | arranca el servicio sin abrir la página |
| `xport stop` | cierra el servicio |
| `xport status` | dice si está corriendo |
| `xport check` | dice si está todo lo que hace falta y dónde están tus datos |

En Windows el comando es `%LOCALAPPDATA%\Programs\xport\xport.cmd`; en macOS,
`/Applications/xport.app/Contents/MacOS/xport`.

## Actualizar y desinstalar

**Actualizar:** se instala la versión nueva encima, igual que la primera vez. Tus datos no se tocan.

**Desinstalar:**

- Linux: `~/.local/lib/xport/uninstall.sh`.
- Windows: `%LOCALAPPDATA%\Programs\xport\uninstall.cmd`.
- macOS: `xport stop` y arrastrá `xport.app` a la papelera.

Tus datos (el registro del tope, los tweets, los CSV y la sesión de X) quedan en tu carpeta de datos, por si
reinstalás: `~/.local/share/xport` en Linux, `%LOCALAPPDATA%\xport` en Windows y
`~/Library/Application Support/xport` en macOS. Para borrarlos, se borra esa carpeta.

## Armar el instalador

El paquete se arma en el mismo sistema para el que es, porque algunas dependencias vienen compiladas. Hacen falta git,
[uv](https://docs.astral.sh/uv/) y Node 22 o más nuevo:

- Linux: con el gestor de paquetes de tu distribución, o [fnm](https://github.com/Schniz/fnm) para Node.
- macOS: con [Homebrew](https://brew.sh), `brew install uv node`.
- Windows: `winget install astral-sh.uv`, `winget install OpenJS.NodeJS.LTS` y `winget install Git.Git`.

```sh
git clone https://github.com/bafif/xport
cd xport/extension
npm ci
npm run build:chrome
cd ..
uv run python deploy/bundle/build.py --smoke
```

Los comandos son los mismos en Linux, macOS y Windows (en PowerShell). `--smoke` prueba el paquete armado: arranca
su servicio en un puerto libre, mira que conteste y lo cierra. En Linux, con Podman, la extensión también se arma con
`bash deploy/extension-build.sh`, sin Node.

El paquete queda en `dist/bundle/`:

| Sistema | Paquete | Para instalarlo |
|---|---|---|
| Linux | `xport-<versión>-linux-x86_64.tar.gz` | `tar -xzf dist/bundle/xport-*-linux-*.tar.gz -C dist/bundle && dist/bundle/xport/install.sh` |
| Windows | `xport-<versión>-windows-x86_64.zip` | descomprimilo y doble clic en `xport\install.cmd` |
| macOS | `xport-<versión>-macos-<arq>.tar.gz` | `tar -xzf dist/bundle/xport-*-macos-*.tar.gz -C /Applications && open /Applications/xport.app` |

## Desde el código, para desarrollar

Sin instalador, xport corre desde el repo:

```sh
uv sync
bash deploy/extension-build.sh        # o: cd extension && npm ci && npm run build
uv run fastapi dev src/tweet_extractor/service/app.py
```

El servicio queda en `http://localhost:8000`, que es también su página. La extensión armada está en
`extension/dist/chrome-mv3` (Chrome y Edge) y en `extension/dist/firefox-mv2` (Firefox: en `about:debugging`, se elige
su `manifest.json`). Las otras formas de levantar el servicio y la configuración están en la
[referencia](REFERENCE.md).

**Que el navegador arranque el servicio solo (opcional):** con el host de native messaging, el navegador levanta el
servicio al abrirse. Se instala una vez con `bash deploy/native-host/install-linux.sh` (Firefox) o
`bash deploy/native-host/install-linux.sh --chrome` (Chrome o Chromium). Con el instalador no hace falta: lo hace el
acceso directo. Detalle y diagnóstico en [deploy/native-host/README.md](../deploy/native-host/README.md).

## Si algo no anda

- **`xport check`** dice qué falta: el navegador, cage o la extensión, con el comando para instalarlo.
- **«No se pudo contactar al servicio»:** abrí xport desde su acceso directo, o con `xport`.
- **Otro xport ya está corriendo:** el servicio no arranca si ya hay uno en `localhost:8000` o `localhost:8080`, para
  no llevar dos registros del tope. `xport stop` cierra el que está.
- Más avisos y qué significan, en [Cómo se usa](USAGE.md#si-algo-no-anda).
