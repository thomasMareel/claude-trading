@echo off
REM Colle les cles de la plateforme configuree (OKX Europe) dans .env, puis les verifie.
REM Pour le bac a sable : scripts\coller_cles_testnet.bat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0coller_cles.ps1"
pause
