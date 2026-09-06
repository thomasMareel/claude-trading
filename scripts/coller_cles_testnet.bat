@echo off
REM Colle les cles du TESTNET de la plateforme configuree.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0coller_cles.ps1" -Testnet
pause
