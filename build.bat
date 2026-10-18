@echo off
REM Compile le launcher en UN seul .exe (dans le dossier dist\).
REM Copie ensuite dist\EZ_Server_Launcher.exe sur le PC serveur : c'est tout.
cd /d "%~dp0"

python -m pip install -r requirements.txt
python -m pip install pyinstaller

pyinstaller --onefile --noconsole --name EZ_Server_Launcher launcher.py

echo.
echo ============================================================
echo  Termine. Le .exe est ici :  dist\EZ_Server_Launcher.exe
echo  Copie-le sur le PC serveur et double-clique dessus.
echo ============================================================
pause
