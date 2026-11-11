# Extension icons

[Español](README.md) · [Back to the extension](../README.en.md)

WXT finds the PNGs in `extension/public/icon/{size}.png` by itself and puts them in the Chrome and Firefox manifests
(the `icons` field). The toolbar uses the same set for the extension's button. There is no need to touch
`wxt.config.ts`.

## How to regenerate them

1. Replace the master, `extension/scripts/icon-master.png` (1024×1024, ideally with a transparent background).
2. From `extension/`, run:

   ```bash
   ./scripts/make-icons.sh
   ```

   It leaves `16.png`, `32.png`, `48.png`, `96.png` and `128.png` in `public/icon/`.
3. `npm run build` (or `npm run dev`) picks them up by itself.

## What each size is for

| Size | Use |
|---|---|
| 16 | favicon and toolbar, 1× scale |
| 32 | toolbar on HiDPI screens and on Windows |
| 48 | extensions page |
| 96 | Firefox on HiDPI |
| 128 | Chrome Web Store and installation |

## What goes into git and what goes into the extension

- **Into git:** the master (`scripts/icon-master.png`) and the `public/icon/{size}.png` files made from it.
- **Into the extension** (what WXT copies to `dist/`): only the `public/icon/{size}.png` files. The master lives in
  `scripts/` so it stays out of the extension: it weighs about 480 KB and is not used.
