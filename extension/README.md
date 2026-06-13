# xport — extensión (Fase 4)

Extensión Chrome (MV3) + Firefox, código compartido vía [WXT](https://wxt.dev).
Implementa el **patrón (a)** del plan: la extensión es un cliente liviano; el
popup pega contra el servicio local de xport (FastAPI) para lanzar jobs, ver el
estado del Compliance Gate y descargar los CSV. No hace scraping por sí misma.

## Requisitos

- Node (ver `.node-version` → 22). En dev se gestiona con `fnm`; en Docker NO se
  usa fnm (se pinea la imagen Node).
- El servicio de xport corriendo (ver raíz del repo): `uv run fastapi dev
  src/tweet_extractor/service/app.py`, o `docker compose -f deploy/docker-compose.yml up`.

## Comandos

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

## Pendiente

- Iconos (`public/icon/*.png`): hoy usa el default del navegador.
- Verificación cargando la extensión en un navegador real (no se pudo automatizar).
- Patrones (b) Native Messaging y (c) captura GraphQL in-page: reservados; si se
  agrega (c), debe reportar sus accesos al Compliance Gate (invariante 1M/24h global).
