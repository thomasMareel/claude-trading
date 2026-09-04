@echo off
REM Boucle de paper trading, relancee automatiquement si elle s'arrete.
REM Toute la sortie part dans logs\loop.log pour garder une trace des incidents.
cd /d "%~dp0"
:loop
echo. >> logs\loop.log
echo ===== demarrage %DATE% %TIME% ===== >> logs\loop.log
.venv\Scripts\python.exe scripts\run_loop.py --paper --now >> logs\loop.log 2>&1
echo ===== arret code %ERRORLEVEL% le %DATE% %TIME% ===== >> logs\loop.log
REM code 2 = coupe-circuit declenche : on n'insiste pas.
if "%ERRORLEVEL%"=="2" goto fin
timeout /t 30 /nobreak >nul
goto loop
:fin
echo Coupe-circuit declenche. Boucle arretee volontairement. >> logs\loop.log
