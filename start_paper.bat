@echo off
REM Lance la boucle de paper trading dans cette fenetre. Ctrl+C pour arreter.
cd /d "%~dp0"
if not exist .env (
  echo Le fichier .env est absent. Copie .env.example en .env et remplis ANTHROPIC_API_KEY.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\run_loop.py --paper --now
pause
