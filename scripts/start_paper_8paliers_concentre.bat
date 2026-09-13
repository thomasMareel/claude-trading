@echo off
REM  La methode a huit barreaux, mille euros PAR PAIRE, sur les dix paires EUR
REM  les plus liquides d'OKX. Dix experiences independantes menees en parallele
REM  sous la meme echelle, pour isoler ce que la paire change : ce n'est pas un
REM  portefeuille de dix mille euros, c'est un dispositif de mesure.
REM  Le classement de liquidite qui choisit les paires est produit par
REM  scripts/classer_liquidite.py et archive dans docs/archives/liquidite.json,
REM  avec sa fenetre datee : un critere sans sa mesure n'est qu'une opinion.
REM  Lance de facon DETACHEE : sinon le processus meurt avec la session.
cd /d C:\Claude\Crypto
set PYTHONIOENCODING=utf-8
:boucle
.venv\Scripts\python.exe scripts\paper_grille.py --profil 8paliers_concentre --intervalle 60 >> docs\data\paper_grille_8paliers_concentre.log 2>&1
REM  Si le programme s'arrete pour une raison quelconque, on le relance.
timeout /t 30 /nobreak > nul
goto boucle
