#!/usr/bin/env bash
# Instala el native-messaging host de xport para un navegador que corre DENTRO de
# Linux/WSL (WSLg). Para un navegador de Windows usá install-windows.ps1 (desde
# PowerShell) — ese caso necesita el bridge .bat → wsl.exe y registro de Windows.
#
# Uso:
#   ./install-linux.sh                      # Firefox, ext id por default (xport@local)
#   ./install-linux.sh --chrome             # Chrome, Chrome Beta/Dev y Chromium
#   ./install-linux.sh --chrome --ext-id=<chrome-id>
#   XPORT_EXT_ID=mi@id ./install-linux.sh
#   ./install-linux.sh --uninstall          # quita el manifest (con --chrome, el de Chrome)
set -euo pipefail

HOST_NAME="com.xport.host"
# Ids por default: el de Firefox lo fija `gecko.id` y el de Chrome la `key`, los dos en
# extension/wxt.config.ts.
FIREFOX_ID="xport@local"
CHROME_ID="aiildgjidjmeilmjinaililpijenmodi"
EXT_ID="${XPORT_EXT_ID:-}"
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

# Directorios de manifests (ubicaciones estándar). Cada variante de Chrome lee el suyo:
# se instala en todas las que tengan carpeta de configuración, y si no hay ninguna, en
# la de Chrome estable.
DIRS=()
if [ "$BROWSER" = "chrome" ]; then
  EXT_ID="${EXT_ID:-$CHROME_ID}"
  for variante in google-chrome google-chrome-beta google-chrome-unstable chromium; do
    if [ -d "$HOME/.config/$variante" ]; then
      DIRS+=("$HOME/.config/$variante/NativeMessagingHosts")
    fi
  done
  if [ "${#DIRS[@]}" -eq 0 ]; then
    DIRS=("$HOME/.config/google-chrome/NativeMessagingHosts")
  fi
  # Chrome autoriza por origin.
  ALLOWED="\"allowed_origins\": [\"chrome-extension://$EXT_ID/\"]"
else
  EXT_ID="${EXT_ID:-$FIREFOX_ID}"
  DIRS=("$HOME/.mozilla/native-messaging-hosts")
  # Firefox autoriza por id de extensión.
  ALLOWED="\"allowed_extensions\": [\"$EXT_ID\"]"
fi

if [ "$UNINSTALL" -eq 1 ]; then
  for dir in "${DIRS[@]}"; do
    rm -f "$dir/$HOST_NAME.json"
    echo "✓ desinstalado: $dir/$HOST_NAME.json"
  done
  exit 0
fi

chmod +x "$WRAPPER" "$SCRIPT_DIR/host.py"

for dir in "${DIRS[@]}"; do
  mkdir -p "$dir"
  cat > "$dir/$HOST_NAME.json" <<JSON
{
  "name": "$HOST_NAME",
  "description": "xport backend launcher (supervisor del FastAPI local)",
  "path": "$WRAPPER",
  "type": "stdio",
  $ALLOWED
}
JSON
done

# Pre-calienta el venv para que el primer arranque del host sea instantáneo y silencioso.
( cd "$REPO_ROOT" && uv sync >/dev/null 2>&1 || true )

echo "✓ instalado ($BROWSER):"
for dir in "${DIRS[@]}"; do
  echo "    manifest: $dir/$HOST_NAME.json"
done
echo "    host:     $WRAPPER  →  uv run python host.py  →  uvicorn $HOST_NAME"
echo "    ext id:   $EXT_ID"
echo ""
echo "Siguiente: cargá/recargá la extensión. Al abrirla, el navegador arranca el backend."
