@echo off
REM Run the web dashboard with the terminal left open for debugging.
cd /d "%~dp0"

set "EZ_WEBAPP_USERNAME=admin"
set "EZ_WEBAPP_PASSWORD=admin"

python -m pip install -r requirements.txt
start "" http://127.0.0.1:8000
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
pause
