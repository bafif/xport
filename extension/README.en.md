# xport: the extension

[Español](README.md) · [Back to the README](../README.en.md)

Extension for Chrome (MV3) and Firefox (MV2), from one codebase thanks to [WXT](https://wxt.dev). It is what captures:

- **It takes work from the service's queue** (`POST /captures/next`) and opens X's search for each capture. It runs in
  xport's Chrome, inside an invisible screen, or in the person's browser when that Chrome is not there.
- **It captures what x.com already downloads:** the content scripts copy the search's GraphQL responses and the
  background sends them to `POST /ingest`. The extension **does not interpret GraphQL**: the service maps them, stores
  them and records the accesses in the cap.
- **It goes through the search by itself:** it scrolls, retries blank loads, carries on from the second of the oldest
  tweet when X stops the search, waits when the search quota runs out and resumes from what was stored
  (`POST /progress`).
- **It stops when the cap cannot count:** with no service, with the cap full or if the service rejects a batch.
- **The popup** queues an account, exports the CSV and shows the cap's status, in English or Spanish.

## Where things are

| File | What it does |
|---|---|
| `entrypoints/background.ts` | the queue, the capture tabs, sending to the service |
| `entrypoints/capture-main.content.ts`, `capture-bridge.content.ts` | the `fetch` and XHR patch on x.com and the bridge to the background |
| `entrypoints/autoscroll.content.ts` | going through the search |
| `entrypoints/popup/` | the popup |
| `lib/` | the pure logic, with its tests: queue, cap, X's limits, account without tweets, language |
| `locales/` | the texts in Spanish and English |

## Commands

Without Node on the machine, from the repository root and with Podman:

```bash
bash deploy/extension-build.sh   # type check, tests and both versions in dist/
```

With Node 26 (the version is in `.node-version`), from `extension/`:

```bash
npm ci                 # dependencies from package-lock.json
npm run dev            # Chrome with hot reload
npm run dev:firefox    # the same in Firefox
npm run compile        # type check
npm test               # tests (vitest)
npm run build          # dist/chrome-mv3 and dist/firefox-mv2
npm run build:chrome   # Chrome only, what the installer uses
npm run zip            # the zips to publish
```

To load it by hand in the browser, see [Install](../docs/INSTALL.en.md#the-extension-in-your-browser).

## Why it needs no CORS

The popup is an extension page. With `host_permissions` on the service (`http://localhost/*`,
`http://127.0.0.1/*`), its `fetch` calls are not subject to CORS. `CORS_ALLOW_ORIGINS` would only be needed if a web
page called the API.

## Icons

WXT picks up `public/icon/{size}.png` and puts them in the Chrome and Firefox manifests. They come from
`scripts/icon-master.png` with `./scripts/make-icons.sh`; details in [scripts/README.en.md](scripts/README.en.md).
