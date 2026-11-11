# Íconos de la extensión

[English](README.en.md) · [Volver a la extensión](../README.md)

WXT encuentra solo los PNG de `extension/public/icon/{tamaño}.png` y los pone en los manifiestos de Chrome y Firefox
(campo `icons`). La barra de herramientas usa ese mismo juego para el botón de la extensión. No hace falta tocar
`wxt.config.ts`.

## Cómo regenerarlos

1. Reemplazá el maestro, `extension/scripts/icon-master.png` (1024×1024, mejor con fondo transparente).
2. Desde `extension/`, corré:

   ```bash
   ./scripts/make-icons.sh
   ```

   Deja `16.png`, `32.png`, `48.png`, `96.png` y `128.png` en `public/icon/`.
3. `npm run build` (o `npm run dev`) los toma solo.

## Para qué sirve cada tamaño

| Tamaño | Uso |
|---|---|
| 16 | favicon y barra de herramientas, escala 1× |
| 32 | barra de herramientas en pantallas HiDPI y en Windows |
| 48 | página de extensiones |
| 96 | Firefox en HiDPI |
| 128 | Chrome Web Store e instalación |

## Qué va en git y qué va en la extensión

- **En git:** el maestro (`scripts/icon-master.png`) y los `public/icon/{tamaño}.png` que salen de él.
- **En la extensión** (lo que WXT copia a `dist/`): solo los `public/icon/{tamaño}.png`. El maestro está en `scripts/`
  para que no entre en la extensión: pesa unos 480 KB y no se usa.
