# Banc d'essai : Claude trade seul, face à un repère

Un système complet pour observer **comment une IA s'organise pour trader**,
sur un petit capital que l'on accepte de perdre, avec des garde-fous
déterministes que personne ne peut contourner.

Un seul trader, Claude. Il reçoit toutes les quatre heures un dossier de
marché, rend des décisions structurées avec son raisonnement écrit, et une
couche de risque relit chaque décision avant exécution.

Un seul repère de rentabilité : **le panier équipondéré des mêmes actifs,
acheté au premier cycle et jamais touché**, aux mêmes frais et au même
slippage, des deux côtés. C'est ce que vous auriez eu en ne faisant rien
d'intelligent. Claude doit faire mieux que ça, net de tout.

Le protocole de mesure, ce qui compte comme un succès et ce que l'on peut
conclure ou non, est dans [docs/protocole.md](docs/protocole.md).

## Ce qu'il faut savoir avant de commencer

- **Le résultat financier ne sera pas statistiquement significatif.** Avec
  100 € sur quelques semaines, le hasard domine. Ce que l'on mesure d'abord,
  c'est le raisonnement, la discipline et la mécanique. Le protocole dit
  précisément ce que l'on a le droit de conclure.
- **Les frais décident de tout à cette taille.** Un aller-retour coûte 0,2 %.
  Le budget est limité à quatre nouvelles positions par semaine, en dur.
- **Claude coûte de l'argent en appels API**, indépendamment du capital tradé.
  Ordre de grandeur avec `claude-opus-5` en effort `medium`, six cycles par
  jour : 10 à 18 $ par mois. Le rapport affiche la performance nette de ce
  coût.
- **Spot uniquement, long uniquement, aucun levier.** Le code refuse de
  démarrer autrement.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Puis créer les clés en suivant [docs/cles-api.md](docs/cles-api.md). Seule la
clé Claude est nécessaire pour le paper trading. Vérifier :

```bash
python scripts/verifier_cles.py
```

## Utilisation

Charger de l'historique une première fois, pour amorcer les indicateurs :

```bash
python scripts/fetch_history.py --days 120
```

Lancer la boucle en continu, détachée, avec redémarrage automatique et
journal dans `logs/loop.log`. C'est le mode normal :

```bat
start_paper_detached.bat
```

Un seul cycle, tout de suite, pour voir le système fonctionner :

```bash
python scripts/run_cycle.py --paper
```

Lire le carnet de bord, le vrai livrable :

```bash
python scripts/journal.py -n 30 --thinking
```

Voir uniquement ce que la couche de risque a refusé, et pourquoi :

```bash
python scripts/journal.py --refused
```

Claude face au repère :

```bash
python scripts/report.py
```

Remettre l'expérience à zéro (paper uniquement, les bougies sont gardées) :

```bash
python scripts/reset_experiment.py --yes
```

Tests, sans réseau :

```bash
python -m pytest tests -q
```

## Architecture

```
config.yaml            tous les réglages
docs/protocole.md      ce que l'on mesure, ce que l'on peut conclure
docs/cles-api.md       créer les clés, pas à pas
src/
  config.py            chargement et validation de la config, secrets (env uniquement)
  exchange.py          accès Binance via ccxt (données, et ordres en live)
  indicators.py        EMA, RSI, ATR, momentum, en pandas pur
  storage.py           SQLite : bougies, décisions, ordres, positions, equity, repère, coûts
  portfolio.py         reconstruction du book depuis le journal (le cash n'est jamais stocké)
  risk.py              la couche de risque : seule autorisée à dire non
  executor.py          exécution paper (simulée) ou live, même interface
  engine.py            un cycle complet, le repère, le chien de garde
  alerts.py            notifications téléphone via ntfy
  brains/
    base.py            contrat : BrainContext -> list[Decision]
    llm_brain.py       Claude, sortie structurée, coût mesuré
scripts/               points d'entrée
tests/                 risque, moteur, cerveau, indicateurs, config, comptabilité des ordres
data/trading.db        la base, seule source de vérité
logs/decisions.jsonl   miroir lisible des décisions (pas un journal de secours)
logs/loop.log          sortie de la boucle détachée
```

### Un cycle

1. Rafraîchir bougies et prix. Les indicateurs sont calculés sur les bougies
   **clôturées** uniquement.
2. Relever le repère buy-and-hold. Au premier cycle, il est constitué :
   capital divisé à parts égales entre les paires, frais et slippage
   d'entrée déduits. Ensuite sa valeur de liquidation, frais et slippage de
   sortie déduits, est relevée à chaque cycle.
3. Coupe-circuit : si le book a perdu plus que le seuil, tout est liquidé et
   plus rien ne repart jamais. C'est un garde-fou de catastrophe, pas un stop.
4. Sorties forcées : stop de perte, objectif de gain.
5. Construction du dossier : book, positions, indicateurs, décisions passées
   et leur résultat, budget restant.
6. Claude décide.
7. La couche de risque relit chaque décision, la redimensionne ou la refuse.
8. Exécution des ventes puis des achats. Ordre et position sont écrits dans
   une seule transaction.
9. Relevé d'equity et résumé.

### Entre deux cycles : le chien de garde

Toutes les cinq minutes, la boucle vérifie les stops, les objectifs et le
coupe-circuit sur les prix courants, sans appeler Claude. Si une paire ne
répond pas, les autres sont quand même vérifiées. Ces sorties apparaissent
dans le journal avec un identifiant de cycle préfixé `WD`.

### Les garde-fous, dans `config.yaml` sous `risk`

| Réglage | Défaut | Effet |
|---|---|---|
| `max_position_pct` | 40 % | part du book maximale sur une position |
| `max_open_positions` | 2 | positions simultanées |
| `max_round_trips_per_week` | 4 | nouvelles positions par semaine, le frein anti-frais |
| `max_daily_loss_pct` | 6 % | au-delà, plus d'achats jusqu'au lendemain |
| `kill_switch_drawdown_pct` | 20 % | coupe-circuit définitif, garde-fou de catastrophe |
| `stop_loss_pct` / `take_profit_pct` | 8 % / 15 % | posés automatiquement sur chaque position |

La configuration est validée au démarrage : une limite absurde (stop à 0 %,
budget à 0, un seul stop qui dépasserait le coupe-circuit) empêche le
programme de démarrer.

### Alertes

Avec `NTFY_TOPIC` dans `.env`, le téléphone reçoit : démarrage du bot,
chaque stop ou objectif atteint, chaque cycle en échec, tout incident
critique. Voir [docs/cles-api.md](docs/cles-api.md), étape 2.

## Passage en réel

Le live exige **deux serrures** volontairement séparées :

1. `engine.mode: "live"` dans `config.yaml`, ou `--live` en ligne de commande.
2. Un fichier `LIVE_ARMED` à la racine, créé à la main.

Sans le fichier, le programme refuse de démarrer et l'explique. Au démarrage
en live, le bot **compare le compte Binance réel au book** et refuse de
tourner s'ils ne correspondent pas.

Ce que le chemin réel fait pour protéger l'argent :

- chaque ordre porte un identifiant déterministe ; sur un délai réseau après
  l'envoi, l'ordre est **retrouvé** au lieu d'être renvoyé en double ;
- si le résultat d'un ordre reste inconnu malgré tout, le book est déclaré
  **incertain** : alerte urgente, achats gelés jusqu'à ce que vous ayez
  vérifié le compte à la main et acquitté avec `scripts/acquitter.py` ;
- les frais prélevés par Binance sur la crypto reçue sont pris en compte : le
  book reflète la quantité réellement détenue.

Avant de créer `LIVE_ARMED` :

- clés Binance **sans droit de retrait**, restreintes à votre IP, vérifiées
  par `scripts/verifier_cles.py` ;
- capital converti en USDT sur le compte spot ;
- un achat et une vente réels réussis sur le **testnet** (`engine.use_testnet:
  true`, clés dédiées) ;
- assez de paper trading pour avoir vu des achats, des ventes, un stop et au
  moins un refus de la couche de risque.

Limite connue et assumée : les stops sont exécutés par le bot, pas déposés
chez Binance. La protection dépend donc de la survie du processus. Ne pas
engager d'argent réel depuis une machine qui peut s'éteindre.

## Ce que l'on ne fait pas ici

- Pas de conseil d'investissement. Le système exécute une expérience, il ne
  recommande rien.
- Pas de manipulation de clés ou d'ordres par l'assistant qui a écrit ce code.
  Les clés sont dans votre `.env`, le fichier `LIVE_ARMED` est le vôtre.
- Pas d'optimisation de paramètres sur le passé. Le repère est passif par
  construction, il ne peut pas être sur-ajusté.
