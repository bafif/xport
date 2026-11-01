# xport: la extensión

Extensión Chrome (MV3) + Firefox, código compartido vía [WXT](https://wxt.dev).
Es el camino principal para capturar tweets:

- **Captura in-page:** los content scripts capturan las
  respuestas GraphQL que x.com ya descarga en la búsqueda y el background las
  manda a `POST /ingest` del servicio local. La extensión **no parsea GraphQL**:
  el backend mapea, guarda y registra los accesos en el Compliance Gate.
- **Crawl adaptativo:** «Capturar» abre la búsqueda de la cuenta y el rango,
  scrollea sola y, si x.com corta antes de llegar a la fecha inicial, achica el
  rango y vuelve a buscar. Si X frena la búsqueda, la próxima captura retoma
  desde lo ya guardado (`POST /progress`).
- **Popup de un solo flujo:** cuenta + desde + hasta → Capturar / Detener /
  Exportar CSV, más el estado del gate.
- **Auto-arranque (opcional):** con el host nativo de
  `deploy/native-host/`, el navegador levanta y baja el servicio solo.

## Requisitos

- Node (ver `.node-version` → 22). En desarrollo la manejo con `fnm`; en Docker no
  hace falta, porque la imagen de Node está fijada.
- El servicio de xport corriendo (ver raíz del repo): `uv run fastapi dev
  src/tweet_extractor/service/app.py`, o `docker compose -f deploy/docker-compose.yml up`.

## Comandos

Sin Node en la máquina, desde la raíz del repo:

```bash
docker build -f deploy/extension.Dockerfile -o extension/dist extension
```

Con Node local:

```bash
fnm use            # fija Node a la versión de .node-version
npm ci             # instala deps desde package-lock.json (corre `wxt prepare`)
npm run dev        # WXT dev (Chrome) con HMR
npm run dev:firefox
npm run compile    # tsc --noEmit (typecheck)
npm run build      # → dist/chrome-mv3 y dist/firefox-mv2
npm run zip        # empaqueta para subir a las stores
```

## Cargar sin firmar (dev)

- **Chrome**: `chrome://extensions` → modo desarrollador → "Cargar descomprimida"
  → `dist/chrome-mv3`.
- **Firefox**: `about:debugging` → "Este Firefox" → "Cargar complemento temporal"
  → `dist/firefox-mv2/manifest.json`.

## Por qué no necesita CORS del servidor

El popup es una *extension page*. Con `host_permissions` sobre el host del
servicio (`http://localhost/*`, `http://127.0.0.1/*`), sus `fetch` cross-origin
no quedan sujetos a CORS. El `CORS_ALLOW_ORIGINS` del FastAPI solo hace falta si
en el futuro un *content script* o una página web llaman a la API.

## Iconos

WXT autodescubre `public/icon/{size}.png` y los cablea solo en los manifests de
Chrome y Firefox (incluido `action.default_icon`, el de la barra de direcciones).
Para regenerarlos: guardá el maestro 1024×1024 en `scripts/icon-master.png` y corré
`./scripts/make-icons.sh` (detecta ImageMagick / `sips` / Pillow). Detalle en
`scripts/README.md`.

## Pendiente

- Una verificación end-to-end documentada del flujo completo en un navegador real.
- El puente de native messaging desde un navegador en Windows hacia WSL.
