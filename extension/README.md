# xport: la extensión

[English](README.en.md) · [Volver al README](../README.md)

Extensión para Chrome (MV3) y Firefox (MV2), con un solo código gracias a [WXT](https://wxt.dev). Es la que captura:

- **Toma el trabajo de la cola** del servicio (`POST /captures/next`) y abre la búsqueda de X de cada captura. Corre
  en el Chrome de xport, adentro de una pantalla invisible, o en el navegador de la persona cuando ese Chrome no está.
- **Captura lo que x.com ya descarga:** los content scripts copian las respuestas GraphQL de la búsqueda y el
  background las manda a `POST /ingest`. La extensión **no interpreta GraphQL**: el servicio las mapea, las guarda y
  registra los accesos en el tope.
- **Recorre la búsqueda sola:** scrollea, reintenta las cargas en blanco, sigue desde el segundo del tweet más viejo
  cuando X frena la búsqueda, espera cuando se acaba el cupo de búsquedas y retoma desde lo guardado
  (`POST /progress`).
- **Corta cuando el tope no puede contar:** sin servicio, con el tope lleno o si el servicio rechaza un lote.
- **El popup** encola una cuenta, exporta el CSV y muestra el estado del tope, en castellano o en inglés.

## Dónde está cada cosa

| Archivo | Qué hace |
|---|---|
| `entrypoints/background.ts` | la cola, las pestañas de captura, el envío al servicio |
| `entrypoints/capture-main.content.ts`, `capture-bridge.content.ts` | el parche de `fetch` y XHR en x.com y el puente al background |
| `entrypoints/autoscroll.content.ts` | el recorrido de la búsqueda |
| `entrypoints/popup/` | el popup |
| `lib/` | la lógica pura, con sus tests: cola, tope, límites de X, cuenta sin tweets, idioma |
| `locales/` | los textos en castellano y en inglés |

## Comandos

Sin Node en la máquina, desde la raíz del repo y con Podman:

```bash
bash deploy/extension-build.sh   # chequeo de tipos, tests y las dos versiones en dist/
```

Con Node 22 (la versión está en `.node-version`), desde `extension/`:

```bash
npm ci                 # dependencias desde package-lock.json
npm run dev            # Chrome con recarga en caliente
npm run dev:firefox    # lo mismo en Firefox
npm run compile        # chequeo de tipos
npm test               # tests (vitest)
npm run build          # dist/chrome-mv3 y dist/firefox-mv2
npm run build:chrome   # solo Chrome, lo que usa el instalador
npm run zip            # los zip para publicar
```

Para cargarla a mano en el navegador, ver [Instalar](../docs/INSTALL.md#la-extensión-en-tu-navegador).

## Por qué no necesita CORS

El popup es una página de la extensión. Con `host_permissions` sobre el servicio (`http://localhost/*`,
`http://127.0.0.1/*`), sus `fetch` no quedan sujetos a CORS. `CORS_ALLOW_ORIGINS` solo haría falta si una página web
llamara a la API.

## Íconos

WXT toma `public/icon/{tamaño}.png` y los pone en los manifiestos de Chrome y Firefox. Salen de
`scripts/icon-master.png` con `./scripts/make-icons.sh`; detalle en [scripts/README.md](scripts/README.md).
