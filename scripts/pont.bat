@echo off
REM Pont GitHub : traite les demandes ouvertes avec Claude. Voir scripts\pont.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pont.ps1"
