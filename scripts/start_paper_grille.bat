@echo off
REM  Fait tourner la grille en paper trading, en continu.
REM  Lance de facon DETACHEE (voir plus bas) : sinon le processus meurt avec la
REM  session qui l'a demarre, et le paper trading perd ses heures.
cd /d C:\Claude\Crypto
set PYTHONIOENCODING=utf-8
:boucle
.venv\Scripts\python.exe scripts\paper_grille.py --intervalle 60 >> docs\data\paper_grille.log 2>&1
REM  Si le programme s'arrete pour une raison quelconque, on le relance.
timeout /t 30 /nobreak > nul
goto boucle
