@echo off
REM Colle la cle Claude dans .env, la verifie, redemarre le bot. La cle est saisie masquee ici.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0coller_cle.ps1"
pause
