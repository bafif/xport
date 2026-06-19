#!/usr/bin/env bash
# Reescala el icono maestro de xport a los tamaños que usan Chrome/Firefox.
#
# WXT autodescubre los PNG en public/icon/{size}.png y los cablea solo en ambos
# manifests (icons + action.default_icon). Así que con dejar las salidas acá
# alcanza: no hay que tocar wxt.config.ts.
#
# Uso:
#   ./scripts/make-icons.sh [maestro.png]
# Default del maestro: scripts/icon-master.png (1024x1024, fondo transparente).
# El maestro vive FUERA de public/ a propósito: public/ se copia tal cual al
# bundle publicado, así que el maestro (~480 KB) no debe estar ahí.
#
# Detecta la primera herramienta disponible: magick > convert > sips > python+Pillow.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="${1:-scripts/icon-master.png}"
OUT_DIR="public/icon"
SIZES=(16 32 48 96 128)

if [[ ! -f "$SRC" ]]; then
  echo "✗ No encuentro el maestro: $SRC" >&2
  echo "  Generá uno a 1024x1024 (fondo transparente) y guardalo en scripts/icon-master.png." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

resize() { # $1=size  $2=out
  if command -v magick >/dev/null 2>&1; then
    magick "$SRC" -resize "${1}x${1}" -strip "$2"
  elif command -v convert >/dev/null 2>&1; then
    convert "$SRC" -resize "${1}x${1}" -strip "$2"
  elif command -v sips >/dev/null 2>&1; then            # macOS, sin instalar nada
    sips -z "$1" "$1" "$SRC" --out "$2" >/dev/null
  elif python3 -c "import PIL" >/dev/null 2>&1; then
    python3 - "$SRC" "$1" "$2" <<'PY'
import sys
from PIL import Image
src, size, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
img = Image.open(src).convert("RGBA")
img.resize((size, size), Image.LANCZOS).save(out)
PY
  elif command -v uv >/dev/null 2>&1; then               # uv: Pillow efímero, sin instalar nada global
    uv run --quiet --with pillow python - "$SRC" "$1" "$2" <<'PY'
import sys
from PIL import Image
src, size, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
Image.open(src).convert("RGBA").resize((size, size), Image.LANCZOS).save(out)
PY
  else
    echo "✗ No hay herramienta de imágenes. Instalá una:" >&2
    echo "    Debian/WSL:  sudo apt install imagemagick" >&2
    echo "    o Pillow:    uv tool install pillow   (o pip install pillow)" >&2
    exit 2
  fi
}

for s in "${SIZES[@]}"; do
  out="$OUT_DIR/${s}.png"
  resize "$s" "$out"
  echo "✓ $out"
done

echo "Listo. 'npm run build' los toma automáticamente (WXT autodescubre public/icon/)."
