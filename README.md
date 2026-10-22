# EZ Minecraft Server Launcher

> Web-first LAN server manager for Minecraft, built on the existing launcher core. It installs the correct Java runtime and server files, configures the server, and lets you administer it from a browser on the local network.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![Web](https://img.shields.io/badge/UI-Web%20Dashboard-4F46E5)
![License](https://img.shields.io/badge/License-All%20rights%20reserved-red)

---

## Overview

This project started as a desktop launcher and has evolved into a browser-based management panel for a self-hosted Minecraft server.

The current version is built around a FastAPI web app in `webapp/` and keeps the real runtime logic in `core/` so the server lifecycle, Java management, downloads, and process control come from the same verified code path.

The browser UI is designed for local network use. It is not intended to be exposed to the public internet without additional security hardening.

---

## Current status

The new version is functional and includes:

- a web dashboard at `/` with separate pages per section
- auth-protected API routes
- config persistence for loader, version, RAM, port and all `server.properties` settings
- server creation, start, stop, restart, and command routes
- live console output (polled by the dashboard; websocket available for scripted clients)
- status reporting for LAN/public addresses and PlayIt metadata
- real install flow before launch instead of demo fallback

The browser dashboard is the active and supported workflow.

---

## What you need to run it

Requirements:

- Windows 10/11 (primary), Linux, or Android with Termux (see below)
- Python 3.13+
- Access to the local LAN or localhost
- Environment variables for the web app auth

Install dependencies:

```bat
python -m pip install -r requirements.txt
```

For development (tests): `python -m pip install -r requirements-dev.txt`.

---

## Android (Termux) quick install

1. Install **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) or
   [GitHub releases](https://github.com/termux/termux-app/releases). Avoid the old Google Play
   build: its package repositories may not work.
2. Open Termux and run these three lines (works on a brand-new Termux with nothing else installed):

   ```bash
   pkg install -y curl
   curl -fsSL https://raw.githubusercontent.com/EFCSANTOS/EasyMCMulti/main/termux-setup.sh -o termux-setup.sh
   bash termux-setup.sh
   ```

   To install the `development` branch instead, use `.../EasyMCMulti/development/termux-setup.sh` in
   the second line and run the third as `EZ_BRANCH=development bash termux-setup.sh`.

The script installs Python, Java 21 and build tools, downloads the app, compiles the Python
packages (10–20 minutes the first time), asks for a dashboard username and password, and adds an
`ezmc` command. Afterwards start the dashboard any time with:

```bash
ezmc
```

### Run 24/7

During setup, answer **yes** to "Install 24/7 mode?". The dashboard then runs as a background
service (`termux-services`) that restarts itself if it stops. Control it with:

| Command | Does |
|---|---|
| `ezmc` / `ezmc start` | start in the background and print the dashboard address |
| `ezmc stop` | stop the dashboard, Minecraft and the PlayIt tunnel |
| `ezmc restart` / `ezmc status` | restart / check the background dashboard |
| `ezmc logs` | follow the log (Ctrl+C to leave) |
| `ezmc run` | run in the current terminal instead |

To start after a reboot, install **Termux:Boot** from the same place as Termux, open it once, and
close it. In the dashboard, open **Server setup → Always-on** and turn on:

- **Start Minecraft with the dashboard**
- **Restart after a crash** (on by default: waits 10 s, pauses after 3 crashes in 10 minutes)
- **Start the PlayIt tunnel with the dashboard**

The dashboard's https link (**Remote access** page) is **on by default** and comes back by itself
after every boot; each restart gives a new link.

### Alerts (free)

Open **Server setup → Alerts**:

- **Email me** — sends alerts from your Gmail using an *app password*
  ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), needs 2-step
  verification). Alerts: server crashed / back online / down (auto-restart paused), **new dashboard
  link** (with the link), PlayIt or dashboard link stopped unexpectedly. Each alert is sent at most
  once every 10 minutes. Other providers: open "Other email provider" and set the SMTP server/port.
- **Heartbeat URL** — for when the whole phone goes down (powered off, no internet, app killed) and
  can't send anything. Create a free check at [healthchecks.io](https://healthchecks.io), paste its
  ping URL; the dashboard checks in every minute and healthchecks.io emails you when it stops.
- **Send test** checks both.

### Updates from the app

**Server setup → Updates** shows the installed version and any new commits on the branch you
installed from (`main` by default; Termux: `EZ_BRANCH`). **Update & restart** pulls them
(fast-forward only), reinstalls Python packages if `requirements.txt` changed, and restarts the
dashboard. Minecraft and PlayIt stop briefly and come back if they were running. Optional switches:
check every 6 hours (with an email if alerts are on) and install automatically when no one is online.
The app refuses to update a copy with local edits or unpushed commits instead of overwriting them.

On Windows, the same Always-on switches work; to start the dashboard at logon, add `run.bat` to
Task Scheduler with `EZ_WEBAPP_USERNAME`/`EZ_WEBAPP_PASSWORD` set as user environment variables.

Notes for phones:

- Minecraft 1.17 – 1.21.x is supported (Java 17/21 from Termux). 1.16 and older need Java 8, which Termux does not provide.
- Give the server 1536–2048 MB in **Server setup**; a phone needs about 3 GB free RAM.
- Keep Termux running and exclude it from Android battery optimization. `ezmc` holds a wake lock while running.
- Android 12+ may kill the server with `[Process completed (signal 9)]` (the "phantom process
  killer"). On Android 14+ turn on **Developer options → Disable child process restrictions**; on
  Android 12–13 run once from a PC: `adb shell settings put global settings_enable_monitor_phantom_procs false`.
- CPU usage shows 0% on Android (the system blocks that counter); memory usage works.

---

## Run the web app

Set credentials before starting the app:

PowerShell:

```powershell
$env:EZ_WEBAPP_USERNAME = "admin"
$env:EZ_WEBAPP_PASSWORD = "your_secure_password"
```

Command Prompt:

```bat
set EZ_WEBAPP_USERNAME=admin
set EZ_WEBAPP_PASSWORD=your_secure_password
```

Then start the web server:

```bat
.venv\Scripts\python.exe -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Or, if using the current environment directly:

```bat
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Open in a browser:

```text
http://localhost:8000
```

For LAN access, use the machine IP instead of `localhost`, for example:

```text
http://192.168.1.15:8000
```

Login with the credentials you set in `EZ_WEBAPP_USERNAME` and `EZ_WEBAPP_PASSWORD`.

---

## Web dashboard capabilities

The app exposes a browser interface with:

- loader selection: vanilla, paper, fabric, forge, neoforge
- version list for the selected loader
- RAM and port configuration
- advanced server settings (MOTD, game mode, difficulty, world type, seed, PvP, whitelist, …)
- save config and create server
- start, stop, and restart server
- console command sending and admin shortcuts (op, kick, ban, time, weather, save)
- live console output
- world backups and restore
- worlds: upload a zipped world, keep several worlds side by side, switch, rename, download, delete
- mods/plugins: browse Modrinth by popularity, category or search (server-compatible only), details
  view, one-click install including required dependencies, remove, `.jar` upload (not for vanilla)
- PlayIt tunnel setup, start, and stop (lets friends join without port forwarding)
- optional https link to manage the dashboard from anywhere (Cloudflare quick tunnel)
- LAN/public address display, online player list, and live CPU/RAM charts

---

## Real server lifecycle flow

The browser app uses the same core runtime logic as the launcher:

1. load config from the saved profile
2. resolve the chosen loader and version
3. ensure Java is available for the selected Minecraft version
4. install the server when missing
5. accept the EULA if needed
6. launch the server process with the configured RAM
7. stream server output to the browser console
8. stop, restart, or send commands while running

This is handled in `webapp/app.py` and the lower-level logic in `core/`.

---

## Project structure

```text
core/
  config_store.py
  downloaders.py
  java_manager.py
  modrinth.py
  netinfo.py
  paths.py
  playit.py
  server_process.py

webapp/
  app.py
  auth.py
  state.py
  websocket_manager.py
  static/        dashboard.css, dashboard.js
  templates/     login.html

tests/
  test_webapp_smoke.py

docs/knowledge/  maintainer knowledge base

requirements.txt
README.md
run.bat
```

---

## Security note

This app is intended for local or LAN use.

- Basic auth is enabled for the web app
- credentials must be set through environment variables (`run.bat` prompts for them when unset)
- the PlayIt secret key never leaves the host (`/api/playit/config` returns only the address)
- **Dashboard over the internet (Remote access page)** uses a free Cloudflare quick tunnel: an
  `https://…trycloudflare.com` link, so the password is encrypted in transit. The link changes when
  the tunnel restarts. There is **no lockout for wrong passwords yet**: anyone with the link can keep
  guessing. Hardening the login is a **must fix** scheduled for an upcoming release
  (`docs/knowledge/verification.md`). Until then, use a long unique password and keep sharing off
  when you don't need it. (PlayIt is used only for the Minecraft connection: its API no longer
  allows creating the plain TCP tunnel a dashboard would need.)
- backup restore only accepts the current server's own backups and only extracts `world/`
- the dashboard pages themselves are public shells; all data and actions require auth
- avoid exposing the port to the public internet without a proper firewall and reverse-proxy setup

---

## Development and testing

Run the smoke suite:

```bat
.venv\Scripts\python.exe -m pytest -q tests/test_webapp_smoke.py
```

This verifies the web dashboard, status endpoints, config persistence, console websocket, lifecycle routes, and that every API call made by `dashboard.js` maps to a real route. Tests run against a temporary data folder and do not touch `EZ_Server_Data/`.

---

## Example launch in one shell

```powershell
$env:EZ_WEBAPP_USERNAME = "admin"
$env:EZ_WEBAPP_PASSWORD = "very_secure_password"
.venv\Scripts\python.exe -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Then open:

```text
http://localhost:8000
```

---

## License

All rights reserved.
