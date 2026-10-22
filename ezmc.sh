#!/usr/bin/env bash
# Control the EZ Minecraft dashboard on Termux (installed as the `ezmc` command by termux-setup.sh).
#
#   ezmc            same as `ezmc start`
#   ezmc start      start in the background (24/7 mode) or in this terminal if 24/7 mode is off
#   ezmc stop       stop the dashboard (and with it the Minecraft server and PlayIt tunnel)
#   ezmc restart    restart the background dashboard
#   ezmc status     show whether the background dashboard is running
#   ezmc logs       follow the background dashboard's log (Ctrl+C to leave)
#   ezmc run        run in this terminal even when 24/7 mode is installed
set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SERVICE_DIR="$PREFIX/var/service/ezmc"
LOG_FILE="$PREFIX/var/log/sv/ezmc/current"

has_service() {
  [ -d "$SERVICE_DIR" ] && command -v sv >/dev/null 2>&1
}

ensure_service_daemon() {
  # termux-services runs services under runsvdir, normally started when a Termux session opens.
  if ! pgrep -x runsvdir >/dev/null 2>&1; then
    . "$PREFIX/etc/profile.d/start-services.sh"
    sleep 3
  fi
}

dashboard_url() {
  local port=8000
  if [ -f "$APP_DIR/.env" ]; then
    port="$(sed -n 's/^EZ_WEBAPP_PORT=//p' "$APP_DIR/.env" | tail -n 1)"
  fi
  local ip
  ip="$(cd "$APP_DIR" && .venv/bin/python -c 'from core.netinfo import lan_ip; print(lan_ip())' 2>/dev/null || echo localhost)"
  echo "http://$ip:${port:-8000}"
}

require_service() {
  if ! has_service; then
    echo "24/7 mode is not installed. Re-run termux-setup.sh and answer yes to the 24/7 question."
    exit 1
  fi
  ensure_service_daemon
}

case "${1:-start}" in
  start)
    if has_service; then
      ensure_service_daemon
      sv up ezmc
      echo "Dashboard running in the background: $(dashboard_url)"
      echo "Logs: ezmc logs    Stop: ezmc stop"
    else
      exec bash "$APP_DIR/run.sh"
    fi
    ;;
  stop)
    if has_service; then
      ensure_service_daemon
      sv down ezmc
    fi
    termux-wake-unlock 2>/dev/null || true
    echo "Dashboard stopped."
    ;;
  restart)
    require_service
    sv restart ezmc
    echo "Dashboard restarted: $(dashboard_url)"
    ;;
  status)
    require_service
    sv status ezmc
    ;;
  logs)
    require_service
    tail -n 100 -F "$LOG_FILE"
    ;;
  run)
    exec bash "$APP_DIR/run.sh"
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
