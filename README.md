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

- a web dashboard at `/`
- auth-protected API routes
- config persistence for loader, version, and RAM
- server creation, start, stop, and command routes
- live console output via websocket
- status reporting for LAN/public addresses and PlayIt metadata
- real install flow before launch instead of demo fallback

The browser dashboard is the active and supported workflow.

---

## What you need to run it

Requirements:

- Windows 10/11 recommended
- Python 3.13+
- Access to the local LAN or localhost
- Environment variables for the web app auth

Install dependencies:

```bat
python -m pip install -r requirements.txt
```

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
- RAM configuration
- save config
- create server
- start and stop server
- console command sending
- live console output
- LAN/public address display
- PlayIt status reporting

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
  crash_reporter.py
  downloaders.py
  java_manager.py
  mailer.py
  modrinth.py
  netinfo.py
  paths.py
  playit.py
  server_process.py
  sysinfo.py

webapp/
  app.py
  auth.py
  state.py
  websocket_manager.py

requirements.txt
README.md
run.bat
run_debug.bat
```

---

## Security note

This app is intended for local or LAN use.

- Basic auth is enabled for the web app
- credentials must be set through environment variables
- avoid exposing the port to the public internet without a proper firewall and reverse-proxy setup

---

## Development and testing

Run the smoke suite:

```bat
.venv\Scripts\python.exe -m pytest -q tests/test_webapp_smoke.py
```

This verifies the web dashboard, status endpoints, config persistence, console websocket, and lifecycle routes.

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
