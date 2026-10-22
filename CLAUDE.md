# CLAUDE.md — EZ Minecraft Server Launcher

## What this is
Web-first LAN manager for a self-hosted Minecraft server. FastAPI web app (`webapp/`)
delegates runtime logic to `core/`. Not hardened for public internet exposure.

## Run
```powershell
$env:EZ_WEBAPP_USERNAME = "admin"
$env:EZ_WEBAPP_PASSWORD = "your_secure_password"
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

## Test
```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_webapp_smoke.py
```
Tests use a temporary data dir (autouse fixture) — see `docs/knowledge/testing.md`.

Linux / Termux: `bash run.sh` (reads `.env`). Android install: `termux-setup.sh`.

## Git flow
See `docs/knowledge/git-flow.md`. Branch `feat/…`/`fix/…` from `release` → PR into `development`
→ PR the same branch into `release`. Every commit gets `Co-Authored-By: Claude <noreply@anthropic.com>`.
Merge commits only. **Never open or merge a PR into `main`, and never push to `main`.** The owner
does `release` → `main` when it is stable.

## Language
All code comments, log/error messages and UI text are English. No localization layer.

## Knowledge base
Start at `docs/knowledge/index.md`. Known defects: `docs/knowledge/verification.md`.
