# xport: the extension

[Español](README.md)

Chrome (MV3) + Firefox extension, with shared code via [WXT](https://wxt.dev).
It's the main way to capture tweets:

- **In-page capture:** the content scripts capture the
  GraphQL responses that x.com already downloads during search, and the background sends them
  to the local service's `POST /ingest`. The extension **doesn't parse GraphQL**:
  the backend maps, stores, and logs the accesses in the Compliance Gate.
- **Adaptive crawl:** "Capture" opens the search for the account and the range,
  scrolls on its own and, if x.com cuts off before reaching the start date, narrows the
  range and searches again. If X throttles the search, the next capture picks up
  from what's already been saved (`POST /progress`).
- **Single-flow popup:** account + from + to → Capture / Stop /
  Export CSV, plus the gate status.
- **Auto-start (optional):** with the native host in
  `deploy/native-host/`, the browser starts and stops the service on its own.

## Requirements

- Node (see `.node-version` → 22). In development I manage it with `fnm`; in a container
  you don't need it, because the Node image is pinned.
- The xport service running (see the repo root): `uv run fastapi dev
  src/tweet_extractor/service/app.py`, or `podman compose -f deploy/compose.yaml up`.

## Commands

Without Node on the machine, from the repo root (Podman):

```bash
bash deploy/extension-build.sh
```

With local Node:

```bash
fnm use            # pins Node to the version in .node-version
npm ci             # installs deps from package-lock.json (runs `wxt prepare`)
npm run dev        # WXT dev (Chrome) with HMR
npm run dev:firefox
npm run compile    # tsc --noEmit (typecheck)
npm run build      # → dist/chrome-mv3 and dist/firefox-mv2
npm run zip        # packages for uploading to the stores
```

## Loading unsigned (dev)

- **Chrome**: `chrome://extensions` → developer mode → "Load unpacked"
  → `dist/chrome-mv3`.
- **Firefox**: `about:debugging` → "This Firefox" → "Load Temporary Add-on"
  → `dist/firefox-mv2/manifest.json`.

## Why it doesn't need CORS from the server

The popup is an *extension page*. With `host_permissions` on the service's
host (`http://localhost/*`, `http://127.0.0.1/*`), its cross-origin `fetch` calls
aren't subject to CORS. FastAPI's `CORS_ALLOW_ORIGINS` is only needed if
a *content script* or a web page calls the API in the future.

## Icons

WXT auto-discovers `public/icon/{size}.png` and wires them up on its own in the Chrome and Firefox manifests
(including `action.default_icon`, the one in the address bar).
To regenerate them: save the 1024×1024 master to `scripts/icon-master.png` and run
`./scripts/make-icons.sh` (it detects ImageMagick / `sips` / Pillow). Details in
`scripts/README.en.md`.

## To do

- A documented end-to-end check of the full flow in a real browser.
- The native messaging bridge from a browser on Windows to WSL.