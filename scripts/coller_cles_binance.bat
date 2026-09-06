@echo off
REM Colle les cles Binance du COMPTE REEL dans .env, puis les verifie.
REM Pour le bac a sable : scripts\coller_cles_binance_testnet.bat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0coller_cles_binance.ps1"
pause
