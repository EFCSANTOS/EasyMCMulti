#!/usr/bin/env bash
# Start the EZ Minecraft web dashboard on Linux / Termux.
# Login and port come from .env next to this script (written by termux-setup.sh) or the environment.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
: "${EZ_WEBAPP_USERNAME:?Set EZ_WEBAPP_USERNAME in .env or the environment (or run termux-setup.sh)}"
: "${EZ_WEBAPP_PASSWORD:?Set EZ_WEBAPP_PASSWORD in .env or the environment (or run termux-setup.sh)}"
export EZ_WEBAPP_USERNAME EZ_WEBAPP_PASSWORD
export EZ_WEBAPP_PORT="${EZ_WEBAPP_PORT:-8000}"

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

# Keep Android from suspending Termux while the server runs.
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
  trap 'termux-wake-unlock' EXIT
fi

lan_ip="$("$PY" -c 'from core.netinfo import lan_ip; print(lan_ip())' 2>/dev/null || echo localhost)"
printf '\nDashboard: http://%s:%s  (press Ctrl+C to stop)\n\n' "$lan_ip" "$EZ_WEBAPP_PORT"
"$PY" -m uvicorn webapp.app:app --host 0.0.0.0 --port "$EZ_WEBAPP_PORT"
