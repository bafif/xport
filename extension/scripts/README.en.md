# Extension icons

[Español](README.md)

WXT **auto-discovers** the PNGs in `extension/public/icon/{size}.png` and wires
them into the Chrome and Firefox manifests (the `icons` field). The toolbar uses
that set as the fallback for the action button. No need to touch `wxt.config.ts`.

## How to regenerate them

1. Update the master at `extension/scripts/icon-master.png` (1024×1024, ideally
   with a transparent background).
2. Run from `extension/`:

   ```bash
   ./scripts/make-icons.sh
   ```

   That produces `16.png 32.png 48.png 96.png 128.png` in `public/icon/`.
3. `npm run build` (or `npm run dev`) picks them up automatically.

## Sizes and what each one is for

| Size | Use |
|------|-----|
| 16   | favicon / toolbar, 1× scale |
| 32   | toolbar on HiDPI, Windows |
| 48   | extensions (management) page |
| 96   | Firefox HiDPI |
| 128  | Chrome Web Store / installation |

## What gets committed and what gets shipped

- **Committed** (to git): `scripts/icon-master.png` (the source) and the
  `public/icon/{size}.png` files (the outputs).
- **Shipped in the bundle** (what WXT copies to `dist/`): ONLY the
  `public/icon/{size}.png` files. The master lives in `scripts/` precisely so
  it doesn't end up inside the published extension (it weighs ~480 KB and isn't used at runtime).