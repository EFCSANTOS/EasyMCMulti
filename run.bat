@echo off
REM Launch the EZ Minecraft web dashboard and open it in the default browser.
cd /d "%~dp0"

set "EZ_WEBAPP_USERNAME=admin"
set "EZ_WEBAPP_PASSWORD=admin"

python -m pip install -r requirements.txt

start "" http://127.0.0.1:8000
start "" .venv\Scripts\python.exe -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000

exit /b 0
