#!/usr/bin/env bash
# Instala el native-messaging host de xport para un navegador que corre DENTRO de
# Linux/WSL (WSLg). Para Firefox corriendo en Windows usá install-windows.ps1 (desde
# PowerShell) — ese caso necesita el bridge .bat → wsl.exe y registro de Windows.
#
# Uso:
#   ./install-linux.sh                      # Firefox, ext id por default (xport@local)
#   ./install-linux.sh --chrome --ext-id=<chrome-id>
#   XPORT_EXT_ID=mi@id ./install-linux.sh
#   ./install-linux.sh --uninstall          # quita el manifest
set -euo pipefail

HOST_NAME="com.xport.host"
EXT_ID="${XPORT_EXT_ID:-xport@local}"
BROWSER="firefox"
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --chrome) BROWSER="chrome" ;;
    --firefox) BROWSER="firefox" ;;
    --uninstall) UNINSTALL=1 ;;
    --ext-id=*) EXT_ID="${arg#*=}" ;;
    *) echo "arg desconocido: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
WRAPPER="$SCRIPT_DIR/xport-host.sh"

# Directorio de manifests por navegador (ubicaciones estándar de WebExtensions):
case "$BROWSER" in
  firefox) DIR="$HOME/.mozilla/native-messaging-hosts" ;;
  chrome)  DIR="$HOME/.config/google-chrome/NativeMessagingHosts" ;;
esac
MANIFEST="$DIR/$HOST_NAME.json"

if [ "$UNINSTALL" -eq 1 ]; then
  rm -f "$MANIFEST"
  echo "✓ desinstalado: $MANIFEST"
  exit 0
fi

chmod +x "$WRAPPER" "$SCRIPT_DIR/host.py"
mkdir -p "$DIR"

# Firefox autoriza por id de extensión (allowed_extensions); Chrome por origin.
if [ "$BROWSER" = "chrome" ]; then
  ALLOWED="\"allowed_origins\": [\"chrome-extension://$EXT_ID/\"]"
else
  ALLOWED="\"allowed_extensions\": [\"$EXT_ID\"]"
fi

cat > "$MANIFEST" <<JSON
{
  "name": "$HOST_NAME",
  "description": "xport backend launcher (supervisor del FastAPI local)",
  "path": "$WRAPPER",
  "type": "stdio",
  $ALLOWED
}
JSON

# Pre-calienta el venv para que el primer arranque del host sea instantáneo y silencioso.
( cd "$REPO_ROOT" && uv sync >/dev/null 2>&1 || true )

echo "✓ instalado ($BROWSER):"
echo "    manifest: $MANIFEST"
echo "    host:     $WRAPPER  →  uv run python host.py  →  uvicorn $HOST_NAME"
echo "    ext id:   $EXT_ID"
echo ""
echo "Siguiente: cargá/recargá la extensión. Al abrirla, el navegador arranca el backend."
