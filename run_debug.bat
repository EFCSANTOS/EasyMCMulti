@echo off
REM Version debug : garde le terminal ouvert pour voir les erreurs Python.
cd /d "%~dp0"
python -m pip install -r requirements.txt
python launcher.py
pause
