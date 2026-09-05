# Claude trading automatisé : tous les liens

Un seul point d'entrée pour tout ce qui a été construit. À garder sous la main.

## 1. La page, votre outil unique

| Quoi | Lien |
|---|---|
| **Le tableau de bord** (état du bot, courbes, journal, mandats, critères du protocole) | https://thomasmareel.github.io/claude-trading/ |
| Demander quelque chose à Claude (ouvre une demande GitHub pré-remplie) | https://github.com/thomasMareel/claude-trading/issues/new?template=demande.yml |
| Ouvrir Claude Code pour discuter directement | https://claude.ai/code |

La page se met à jour toute seule après chaque cycle du bot, toutes les quatre heures.

## 2. Le dépôt GitHub (public)

| Quoi | Lien |
|---|---|
| Le code, la documentation, les relevés | https://github.com/thomasMareel/claude-trading |
| Les demandes en cours et passées | https://github.com/thomasMareel/claude-trading/issues |
| Les pull requests proposées par Claude, à valider | https://github.com/thomasMareel/claude-trading/pulls |
| L'historique des commits (les « relevé … UTC » sont les publications automatiques du bot) | https://github.com/thomasMareel/claude-trading/commits/master |
| Réglage de la page GitHub Pages | https://github.com/thomasMareel/claude-trading/settings/pages |

Rappel : tout ce qui est dans ce dépôt est visible par n'importe qui. Aucun secret n'y est, ni n'y a jamais été.

## 3. Les documents à lire

| Document | En ligne | Sur ce PC |
|---|---|---|
| Mode d'emploi général | https://github.com/thomasMareel/claude-trading/blob/master/README.md | `C:\Claude\Crypto\README.md` |
| **Le protocole de mesure** : ce que l'on mesure, ce que l'on a le droit de conclure | https://github.com/thomasMareel/claude-trading/blob/master/docs/protocole.md | `C:\Claude\Crypto\docs\protocole.md` |
| **Créer les clés, pas à pas** (Claude, alertes, Binance, testnet, pont) | https://github.com/thomasMareel/claude-trading/blob/master/docs/cles-api.md | `C:\Claude\Crypto\docs\cles-api.md` |
| Les fiches de mandats (une par fichier, documentation et configuration) | https://github.com/thomasMareel/claude-trading/tree/master/strategies | `C:\Claude\Crypto\strategies\` |
| La configuration | https://github.com/thomasMareel/claude-trading/blob/master/config.yaml | `C:\Claude\Crypto\config.yaml` |
| Le relevé d'audit du 4 septembre (rapport ponctuel, avant la refonte) | https://claude.ai/code/artifact/1fd0b729-2263-4ca3-9fbc-e355e8ae2bf1 | |

## 4. Sur ce PC : ce qu'il faut savoir lancer

Tout se lance depuis `C:\Claude\Crypto`.

| Pour | Faire |
|---|---|
| Démarrer ou redémarrer le bot (fenêtre noire, redémarrage automatique) | double-cliquer `start_paper_detached.bat` |
| Vérifier les clés sans les afficher | `.venv\Scripts\python.exe scripts\verifier_cles.py` |
| Lire le journal des décisions dans le terminal | `.venv\Scripts\python.exe scripts\journal.py -n 30 --thinking` |
| Claude face au repère | `.venv\Scripts\python.exe scripts\report.py` |
| Les métriques du protocole | `.venv\Scripts\python.exe scripts\metriques.py` |
| Les mille singes (en fin de fenêtre seulement) | `.venv\Scripts\python.exe scripts\singe.py` |
| Voir les mandats, clore une fenêtre, changer de mandat | `.venv\Scripts\python.exe scripts\fenetre.py` puis `--clore --mandat <id> --yes` |
| Forcer la publication des relevés sur la page | `.venv\Scripts\python.exe scripts\publier.py` |
| Traiter les demandes GitHub avec Claude (le pont) | `scripts\pont.bat` |
| Acquitter un incident « book incertain » après vérification manuelle | `.venv\Scripts\python.exe scripts\acquitter.py --yes` |
| Lancer les tests | `.venv\Scripts\python.exe -m pytest tests -q` |

| Fichier | Rôle |
|---|---|
| `.env` | vos clés. Jamais partagé, jamais commité. |
| `data\trading.db` | la base, seule source de vérité |
| `logs\loop.log` | tout ce que le bot a affiché, redémarrages compris |
| `logs\pont.log` | ce que le pont a fait |
| `logs\decisions.jsonl` | miroir lisible des décisions (pas un journal de secours) |
| `LIVE_ARMED` | n'existe pas. Le créer à la main, c'est passer en argent réel. |

## 5. Les comptes et services à configurer, dans l'ordre

| Étape | Où | Pourquoi |
|---|---|---|
| **Clé Claude** (indispensable, sinon Claude ne trade pas) | https://console.anthropic.com | compte API prépayé, distinct de l'abonnement ; charger 20 $, poser une limite mensuelle |
| Alertes sur le téléphone | application **ntfy** (Play Store / App Store) | un sujet secret à coller dans `.env` |
| Reconnecter Claude en ligne de commande (pour le pont) | un terminal, taper `claude` | la connexion actuelle a expiré |
| Clés Binance (plus tard, pour le réel) | https://www.binance.com > profil > API Management | sans droit de retrait, restreintes à votre IP |
| Testnet Binance (avant le réel) | https://testnet.binance.vision | de vrais ordres avec de la fausse monnaie |
| Votre adresse IP publique (pour la restriction Binance) | https://ifconfig.me | |
| Routine Claude dans le cloud (alternative au pont local, optionnel) | https://claude.ai/code/onboarding?magic=github-app-setup puis https://claude.ai/code/routines | exige l'application GitHub de Claude sur le dépôt |

## 6. Où en est-on

- Le bot tourne en mode papier et publie ses relevés. Sans clé Claude, il s'abstient à chaque cycle et le dit.
- L'expérience ne commence (t0) qu'au premier cycle où Claude répond vraiment. Ce jour-là, le repère se constitue et la fenêtre s'ouvre.
- Le mandat actif est `libre`, le témoin. Pour en choisir un autre, lire les fiches sur la page, puis `scripts\fenetre.py`.
- Le protocole est figé : on ne change ni la configuration, ni le mandat, ni le prompt en cours de fenêtre. Le verdict se lit avec `docs\protocole.md` à côté, jamais sans.
