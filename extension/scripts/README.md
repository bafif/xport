# Iconos de la extensión

WXT **autodescubre** los PNG de `extension/public/icon/{size}.png` y los cablea
en los manifests de Chrome y Firefox (campo `icons`). La barra de herramientas usa
ese set como fallback del botón de acción. No hace falta tocar `wxt.config.ts`.

## Cómo regenerarlos

1. Actualizá el maestro en `extension/scripts/icon-master.png` (1024×1024, fondo
   transparente idealmente).
2. Corré desde `extension/`:

   ```bash
   ./scripts/make-icons.sh
   ```

   Eso produce `16.png 32.png 48.png 96.png 128.png` en `public/icon/`.
3. `npm run build` (o `npm run dev`) los toma automáticamente.

## Tamaños y para qué sirve cada uno

| Tamaño | Uso |
|--------|-----|
| 16     | favicon / barra de herramientas, escala 1× |
| 32     | barra de herramientas en HiDPI, Windows |
| 48     | página de extensiones (gestión) |
| 96     | Firefox HiDPI |
| 128    | Chrome Web Store / instalación |

## Qué se commitea y qué se publica

- **Se commitean** (en git): `scripts/icon-master.png` (la fuente) y los
  `public/icon/{size}.png` (las salidas).
- **Se publica en el bundle** (lo que WXT copia a `dist/`): SOLO los
  `public/icon/{size}.png`. El maestro vive en `scripts/` justamente para que
  no termine dentro de la extensión publicada (pesa ~480 KB y no se usa en runtime).
