# CLAUDE.md — EZ Minecraft Server Launcher

## What this is
Web-first LAN manager for a self-hosted Minecraft server. FastAPI web app (`webapp/`)
delegates runtime logic to `core/`. Not hardened for public internet exposure.

## Run
```powershell
$env:EZ_WEBAPP_USERNAME = "admin"
$env:EZ_WEBAPP_PASSWORD = "your_secure_password"
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000