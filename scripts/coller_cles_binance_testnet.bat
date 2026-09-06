@echo off
REM Colle les cles du TESTNET Binance (fausse monnaie) dans .env, puis les verifie.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0coller_cles_binance.ps1" -Testnet
pause
