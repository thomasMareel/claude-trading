@echo off
REM  Le meme paper trading, avec l'echelle a huit barreaux, en parallele de
REM  l'autre : memes paires, meme budget, meme pas. Seule la forme de l'echelle
REM  change, sinon la comparaison ne voudrait rien dire.
REM  Son t0 est le 8 septembre 2026 a 20h40 UTC, une heure APRES celui de
REM  l'echelle a trois barreaux : c'est l'instant ou son reglage a ete fige, et
REM  le faire demarrer plus tot reviendrait a la juger sur une heure que son
REM  auteur avait deja sous les yeux.
REM  Lance de facon DETACHEE : sinon le processus meurt avec la session.
cd /d C:\Claude\Crypto
set PYTHONIOENCODING=utf-8
:boucle
.venv\Scripts\python.exe scripts\paper_grille.py --profil 8paliers --intervalle 60 >> docs\data\paper_grille_8paliers.log 2>&1
REM  Si le programme s'arrete pour une raison quelconque, on le relance.
timeout /t 30 /nobreak > nul
goto boucle
