@echo off
REM Lance le launcher SANS terminal qui traine (pythonw = pas de console).
cd /d "%~dp0"
python -m pip install -r requirements.txt
start "" pythonw launcher.py
