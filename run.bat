@echo off
REM Launch the EZ Minecraft web dashboard and open it in the default browser.
REM Credentials come from EZ_WEBAPP_USERNAME / EZ_WEBAPP_PASSWORD; if unset, you are prompted.
cd /d "%~dp0"

if not defined EZ_WEBAPP_USERNAME set /p "EZ_WEBAPP_USERNAME=Dashboard username: "
if not defined EZ_WEBAPP_PASSWORD (
    for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s = Read-Host 'Dashboard password' -AsSecureString; [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "EZ_WEBAPP_PASSWORD=%%p"
)
if not defined EZ_WEBAPP_USERNAME goto :missing
if not defined EZ_WEBAPP_PASSWORD goto :missing

python -m pip install -r requirements.txt

start "" http://127.0.0.1:8000
start "" .venv\Scripts\python.exe -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000

exit /b 0

:missing
echo A username and password are required.
exit /b 1
