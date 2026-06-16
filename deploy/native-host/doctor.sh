#!/usr/bin/env bash
# Doctor del native host — lado WSL/Linux: verifica que el BACKEND puede arrancar y,
# si :8000 está libre, hace un auto-test real del supervisor (arranque→/healthz→EOF→
# shutdown) SIN navegador. Para el lado Windows (registro + manifest + bridge) usá
# doctor.ps1 desde PowerShell. Uso: ./doctor.sh [--no-live]
set -uo pipefail  # sin -e: corremos TODOS los checks aunque alguno falle

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
WRAPPER="$SCRIPT_DIR/xport-host.sh"
LOG="$HOME/.local/state/xport/native-host.log"
LIVE=1
[ "${1:-}" = "--no-live" ] && LIVE=0

fail=0
warn=0
# Color solo si stdout es una terminal (evita escape-garbage al pipear a archivo).
if [ -t 1 ]; then C0=$'\033[0m'; CG=$'\033[32m'; CR=$'\033[31m'; CY=$'\033[33m'; CC=$'\033[36m'
else C0=''; CG=''; CR=''; CY=''; CC=''; fi
ok()   { printf '  %s✓%s %s\n' "$CG" "$C0" "$1"; }
no()   { printf '  %s✗%s %s\n' "$CR" "$C0" "$1"; fail=$((fail + 1)); }
wn()   { printf '  %s!%s %s\n' "$CY" "$C0" "$1"; warn=$((warn + 1)); }
info() { printf '  %si%s %s\n' "$CC" "$C0" "$1"; }

echo "xport native-host doctor (WSL)  ·  repo: $REPO_ROOT"
echo ""

echo "[1] toolchain"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then ok "uv: $(command -v uv) ($(uv --version 2>&1))"; else no "uv no está en PATH — el host no podrá arrancar"; fi

echo "[2] archivos del host"
[ -f "$SCRIPT_DIR/host.py" ] && ok "host.py presente" || no "falta host.py"
if [ -x "$WRAPPER" ]; then ok "xport-host.sh presente + ejecutable"
elif [ -f "$WRAPPER" ]; then wn "xport-host.sh presente pero NO ejecutable (chmod +x)"
else no "falta xport-host.sh"; fi

echo "[3] venv / imports"
if (cd "$REPO_ROOT" && uv run python -c "import uvicorn, tweet_extractor.service.app" >/dev/null 2>&1); then
  ok "uvicorn + tweet_extractor importan en el venv"
else
  no "no importan — corré 'uv sync' en $REPO_ROOT"
fi

echo "[4] manifest local (solo si el navegador corre DENTRO de WSL/WSLg)"
FF="$HOME/.mozilla/native-messaging-hosts/com.xport.host.json"
CH="$HOME/.config/google-chrome/NativeMessagingHosts/com.xport.host.json"
if [ -f "$FF" ]; then
  res="$(python3 - "$FF" "$WRAPPER" <<'PY'
import json, os, sys
f, wrapper = sys.argv[1], sys.argv[2]
try:
    m = json.load(open(f))
except Exception as e:
    print(f"BADJSON {e}"); sys.exit(0)
prob = []
if m.get("name") != "com.xport.host": prob.append(f"name={m.get('name')}")
p = m.get("path", "")
if p != wrapper: prob.append(f"path={p}")
if not (os.path.isfile(p) and os.access(p, os.X_OK)): prob.append("path no existe/no +x")
if "xport@local" not in (m.get("allowed_extensions") or []): prob.append(f"allowed={m.get('allowed_extensions')}")
print("OK" if not prob else "PROB " + " ; ".join(prob))
PY
)"
  [ "$res" = "OK" ] && ok "manifest Firefox-WSL OK ($FF)" || no "manifest Firefox-WSL: $res"
else
  wn "sin manifest Firefox-WSL — esperado si Firefox corre en Windows (allá usá doctor.ps1)"
fi
[ -f "$CH" ] && info "además hay manifest Chrome-WSL ($CH)"

echo "[5] servicio en :8000"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8000/healthz)"
if [ "$code" = "200" ]; then
  cap="$(curl -s --max-time 2 http://127.0.0.1:8000/gate | python3 -c 'import sys,json; print(json.load(sys.stdin).get("hard_cap","?"))' 2>/dev/null)"
  if [ -n "$cap" ] && [ "$cap" != "?" ]; then ok "xport vivo en :8000 (gate hard_cap=$cap)"; else wn ":8000 da 200 pero /gate no parece xport"; fi
elif [ "$code" = "000" ]; then
  info "nada en :8000 (normal si Firefox está cerrado / no cargaste la extensión)"
else
  wn ":8000 responde $code — ¿otro servicio ahí?"
fi

echo "[6] auto-test del supervisor (arranque→/healthz→EOF→shutdown)"
if [ "$LIVE" = "0" ]; then
  info "salteado (--no-live)"
elif [ "$code" != "000" ]; then
  info "salteado: ya hay algo en :8000 (no lo piso). Recorré con :8000 libre para probarlo."
elif WRAPPER="$WRAPPER" python3 - <<'PY'
import os, subprocess, sys, time, urllib.request
def healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False
p = subprocess.Popen(["bash", os.environ["WRAPPER"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
up = False
for _ in range(80):
    if healthy(): up = True; break
    if p.poll() is not None: break
    time.sleep(0.5)
p.stdin.close()  # simula el navegador cerrando el puerto
down = False
for _ in range(40):
    if not healthy(): down = True; break
    time.sleep(0.5)
try:
    rc = p.wait(timeout=10)
except subprocess.TimeoutExpired:
    p.kill(); rc = p.wait()
out = p.stdout.read(200)
print(f"  arrancó+healthz={up}  bajó-en-EOF={down}  rc={rc}  stdout-limpio={out == b''}")
sys.exit(0 if (up and down and rc == 0 and out == b"") else 1)
PY
then ok "el supervisor levanta y baja uvicorn correctamente"
else no "el auto-test del supervisor falló (mirá el detalle de arriba y el log)"
fi

echo ""
echo "[log] $LOG"
[ -f "$LOG" ] && tail -5 "$LOG" | sed 's/^/    /' || echo "    (sin log todavía)"

echo ""
if [ "$fail" -gt 0 ]; then echo "RESULT: FAIL ($fail) · WARN ($warn)"; exit 1
elif [ "$warn" -gt 0 ]; then echo "RESULT: OK con avisos · WARN ($warn)"; exit 0
else echo "RESULT: OK"; exit 0; fi
