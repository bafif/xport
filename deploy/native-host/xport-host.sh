#!/usr/bin/env bash
# Wrapper del native-messaging host de xport. Lo invoca el NAVEGADOR (no vos):
#   - Firefox/Chrome dentro de WSL (WSLg) o Linux nativo: el manifest apunta acá.
#   - Firefox en Windows: el .bat hace `wsl.exe -- bash xport-host.sh` y wsl.exe
#     bridgea stdin/stdout (el canal nativo) hasta este proceso Linux.
#
# stdin/stdout es el canal de native messaging: NO escribir a stdout (los errores van
# a stderr, que el navegador ignora). Localiza `uv` —que puede no estar en el PATH de
# un proceso lanzado por el navegador— y entra al venv del proyecto via `uv run`.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"

# El navegador hereda un PATH mínimo: sumamos las ubicaciones típicas de uv.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "xport-host: no encuentro 'uv' en PATH ($PATH)" >&2
  exit 127
fi

cd "$REPO_ROOT"
# `uv run python` activa el venv del proyecto → uvicorn es importable dentro de host.py.
# `exec` reemplaza este shell: el EOF de stdin del navegador llega directo a host.py.
exec uv run python "$SCRIPT_DIR/host.py" "$@"
