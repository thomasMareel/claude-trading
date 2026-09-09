@echo off
REM  La methode a huit barreaux dans sa FORME FORTE : les mille euros sur une
REM  seule paire, ce qui laisse enfin la place a une raison de 1,6 — mises de
REM  14 a 384 EUR, les grosses tout en bas. A 200 EUR par paire, le plancher de
REM  12 EUR par ordre interdisait cette progression ; c'est la contrainte, pas
REM  un choix, et concentrer est la seule facon de la lever.
REM  Ce que cela coute : la repartition, seule protection gratuite du dispositif.
REM  La paire est BTC/EUR, choisie sur la liquidite connue d'avance et jamais
REM  sur ses resultats passes.
REM  Lance de facon DETACHEE : sinon le processus meurt avec la session.
cd /d C:\Claude\Crypto
set PYTHONIOENCODING=utf-8
:boucle
.venv\Scripts\python.exe scripts\paper_grille.py --profil 8paliers_concentre --intervalle 60 >> docs\data\paper_grille_8paliers_concentre.log 2>&1
REM  Si le programme s'arrete pour une raison quelconque, on le relance.
timeout /t 30 /nobreak > nul
goto boucle
