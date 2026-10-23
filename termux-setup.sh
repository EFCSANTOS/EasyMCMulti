#!/usr/bin/env bash
# One-time installer for EZ Minecraft Server on Android (Termux).
# Works on a brand-new Termux (F-Droid or GitHub build) with nothing else installed:
#
#   pkg install -y curl
#   curl -fsSL https://raw.githubusercontent.com/EFCSANTOS/EasyMCMulti/main/termux-setup.sh -o termux-setup.sh
#   bash termux-setup.sh
#
# (Piping straight into bash also works: curl -fsSL <url> | bash)
#
# Safe to re-run: it updates the code and dependencies and keeps your login (.env).
# Overrides: EZ_REPO_URL, EZ_BRANCH, EZ_APP_DIR, EZ_JAVA_PACKAGE (default openjdk-21).
set -euo pipefail

REPO_URL="${EZ_REPO_URL:-https://github.com/EFCSANTOS/EasyMCMulti.git}"
BRANCH="${EZ_BRANCH:-main}"
APP_DIR="${EZ_APP_DIR:-$HOME/EasyMCMulti}"
JAVA_PACKAGE="${EZ_JAVA_PACKAGE:-openjdk-21}"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

if [ -z "${TERMUX_VERSION:-}" ] && [[ "${PREFIX:-}" != *com.termux* ]]; then
  fail "Run this inside the Termux app (install Termux from F-Droid or GitHub, not the Play Store)."
fi

# Package commands must never prompt: when run as `curl ... | bash`, stdin is this script, so a
# prompt (e.g. "configuration file modified, keep or replace?") would swallow the rest of it.
# --force-confdef/--force-confold answer those prompts automatically; </dev/null guards the rest.
export DEBIAN_FRONTEND=noninteractive
APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

say "Updating Termux packages (a fresh install needs this before anything else)..."
pkg update -y </dev/null
apt-get "${APT_OPTS[@]}" full-upgrade </dev/null

say "Installing system packages (Python, Java, build tools)..."
# rust + binutils: pydantic-core (used by FastAPI) is compiled on the phone.
# proot: provides termux-chroot, which lets the PlayIt agent resolve DNS on Android.
# termux-services: runs the dashboard as a background service for 24/7 mode.
# cloudflared: the optional https link to the dashboard (Remote access page).
apt-get "${APT_OPTS[@]}" install python git "$JAVA_PACKAGE" rust binutils proot termux-tools termux-services \
  cloudflared </dev/null

say "Fetching the app into $APP_DIR..."
if [ -d "$APP_DIR/.git" ]; then
  # Older versions ran `chmod +x` on the scripts; ignore permission bits so that isn't a local edit.
  git -C "$APP_DIR" config core.fileMode false
  git -C "$APP_DIR" pull --ff-only </dev/null
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR" </dev/null
  git -C "$APP_DIR" config core.fileMode false
fi
cd "$APP_DIR"

say "Installing Python packages (first run compiles some of them: 10-20 minutes is normal)..."
[ -x .venv/bin/python ] || python -m venv .venv </dev/null
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
.venv/bin/python -m pip install --upgrade pip </dev/null
# psutil is skipped: Android blocks the CPU counters it reads. The app reads /proc itself instead.
grep -v '^psutil' requirements.txt > .requirements-termux.txt
.venv/bin/python -m pip install -r .requirements-termux.txt </dev/null
rm -f .requirements-termux.txt

env_has_login() {
  [ -f .env ] || return 1
  (
    unset EZ_WEBAPP_USERNAME EZ_WEBAPP_PASSWORD
    . ./.env
    [ -n "${EZ_WEBAPP_USERNAME:-}" ] && [ -n "${EZ_WEBAPP_PASSWORD:-}" ]
  ) 2>/dev/null
}

if ! env_has_login; then
  say "Choose the dashboard login"
  while :; do
    read -rp "Username: " ez_user </dev/tty
    [ -n "$ez_user" ] && break
    echo "The username cannot be empty."
  done
  while :; do
    read -rsp "Password (12+ characters): " ez_pass </dev/tty
    echo
    [ "${#ez_pass}" -ge 12 ] && break
    echo "Too short. This password protects your server if you enable remote access."
  done
  umask 077
  {
    printf 'EZ_WEBAPP_USERNAME=%q\n' "$ez_user"
    printf 'EZ_WEBAPP_PASSWORD=%q\n' "$ez_pass"
    printf 'EZ_WEBAPP_PORT=8000\n'
  } > .env
  unset ez_pass
else
  say "Keeping the existing login in $APP_DIR/.env"
fi

cat > "$PREFIX/bin/ezmc" <<EOF
#!/usr/bin/env bash
exec bash "$APP_DIR/ezmc.sh" "\$@"
EOF
chmod +x "$PREFIX/bin/ezmc"

install_service() {
  # runit service (termux-services): restarts the dashboard whenever it exits.
  local service_dir="$PREFIX/var/service/ezmc"
  mkdir -p "$service_dir/log" "$PREFIX/var/log/sv/ezmc"
  cat > "$service_dir/run" <<EOF
#!$PREFIX/bin/sh
# Started by termux-services; Termux:Boot starts termux-services after a reboot.
export PREFIX="$PREFIX" HOME="$HOME"
export PATH="$PREFIX/bin:\$PATH"
exec 2>&1
exec bash "$APP_DIR/run.sh"
EOF
  chmod +x "$service_dir/run"
  ln -sf "$PREFIX/share/termux-services/svlogger" "$service_dir/log/run"
  rm -f "$service_dir/down"

  # Termux:Boot runs every script in ~/.termux/boot/ after the phone starts.
  mkdir -p "$HOME/.termux/boot"
  cat > "$HOME/.termux/boot/ezmc-boot" <<EOF
#!$PREFIX/bin/sh
termux-wake-lock
. "$PREFIX/etc/profile.d/start-services.sh"
EOF
  chmod +x "$HOME/.termux/boot/ezmc-boot"
}

say "Run 24/7?"
echo "The dashboard can run as a background service that restarts itself if it stops and"
echo "starts again after the phone reboots (needs the free Termux:Boot app, see below)."
always_on=no
if read -rp "Install 24/7 mode? [Y/n] " answer </dev/tty && [[ ! "$answer" =~ ^[Nn] ]]; then
  install_service
  always_on=yes
fi

say "Done!"
if [ "$always_on" = yes ]; then
  cat <<'EOF'
24/7 mode is installed. Commands:
  ezmc          start in the background      ezmc logs     follow the log
  ezmc stop     stop everything              ezmc status   is it running?

To start automatically after a reboot:
  1. Install "Termux:Boot" from the same place you got Termux (F-Droid or GitHub).
  2. Open Termux:Boot once, then close it. That's all.
In the dashboard, open Server setup > Always-on to also start Minecraft and PlayIt by themselves.
EOF
else
  cat <<'EOF'
Start the dashboard any time with:   ezmc
Then open the address it prints from a browser on the same Wi-Fi.
EOF
fi
cat <<'EOF'

Tips:
  * Exclude Termux from battery optimization in Android settings.
  * Android 12+ may kill the server ("signal 9"): see "Android notes" in the README.
  * Phones need ~3 GB free RAM for a small server; set Memory to 1536-2048 MB in Server setup.
EOF

if [ "$always_on" = yes ]; then
  bash ezmc.sh start
elif read -rp $'\nStart it now? [Y/n] ' answer </dev/tty && [[ ! "$answer" =~ ^[Nn] ]]; then
  exec bash run.sh
fi
