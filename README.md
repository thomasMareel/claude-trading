# Banc d'essai : trading crypto automatisé, LLM contre règles

Un système complet pour observer **comment une IA s'organise pour trader**,
sur un petit capital que l'on accepte de perdre, avec des garde-fous
déterministes que personne ne peut contourner.

Deux cerveaux tradent en parallèle, chacun avec la moitié du capital :

| Cerveau | Qui décide | Rôle |
|---|---|---|
| `llm` | Claude, à partir d'un dossier de marché, avec raisonnement écrit | Le sujet de l'expérience |
| `rules` | Un suivi de tendance simple, non optimisé | Le témoin |

Un troisième témoin, la simple détention de bitcoin, est calculé dans le rapport.

## Ce qu'il faut savoir avant de commencer

- **Le résultat financier ne sera pas statistiquement significatif.** Avec 100 € sur
  quelques semaines, le hasard domine. Ce que l'on mesure vraiment, c'est le
  raisonnement, la discipline et la mécanique.
- **Les frais décident de tout à cette taille.** Un aller-retour coûte 0,2 %. Le
  budget est donc limité à quelques nouvelles positions par semaine, en dur.
- **Le cerveau LLM coûte de l'argent en appels API**, indépendamment du capital
  tradé. Ordre de grandeur avec `claude-opus-5` en effort `medium`, six cycles par
  jour : autour de 10 à 15 $ par mois. Un plafond journalier est configuré.
  Pour réduire, baisser `llm.effort` à `low` dans `config.yaml`.
- **Spot uniquement, long uniquement, aucun levier.** Le code refuse de démarrer
  autrement.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Puis remplir `.env` :

- `ANTHROPIC_API_KEY` pour le cerveau LLM. Sans elle, il rend `hold` et le
  journal l'indique, le reste fonctionne.
- Les clés Binance ne sont **pas nécessaires en paper trading**. Elles ne le
  deviennent qu'au passage en live.

## Utilisation

Charger de l'historique une première fois, pour amorcer les indicateurs :

```bash
python scripts/fetch_history.py --days 120
```

Lancer un cycle de décision immédiatement, pour voir le système fonctionner :

```bash
python scripts/run_cycle.py --paper
```

Lancer la boucle continue, un cycle toutes les 4 heures, alignée sur les clôtures
de bougie. `--now` déclenche un premier cycle tout de suite :

```bash
python scripts/run_loop.py --paper --now
```

Lire le carnet de bord, le vrai livrable :

```bash
python scripts/journal.py --brain llm -n 30 --thinking
```

Voir uniquement ce que la couche de risque a refusé, et pourquoi :

```bash
python scripts/journal.py --refused
```

Rapport de performance des deux cerveaux et du témoin :

```bash
python scripts/report.py
```

Tests, sans réseau :

```bash
python -m pytest tests -q
```

## Architecture

```
config.yaml            tous les réglages, aucun chiffre magique dans le code
src/
  config.py            chargement config + secrets (env uniquement)
  exchange.py          accès Binance via ccxt (données, et ordres en live)
  indicators.py        EMA, RSI, ATR, momentum, en pandas pur
  storage.py           SQLite : bougies, décisions, ordres, positions, equity, coûts
  portfolio.py         reconstruction du book depuis le journal (le cash n'est jamais stocké)
  risk.py              la couche de risque : seule autorisée à dire non
  executor.py          exécution paper (simulée) ou live, même interface
  engine.py            un cycle complet, identique en paper et en live
  brains/
    base.py            contrat : BrainContext -> list[Decision]
    llm_brain.py       Claude, sortie structurée, coût mesuré
    rules_brain.py     suivi de tendance témoin
scripts/               points d'entrée
tests/                 couche de risque, moteur, cerveau règles
data/trading.db        la base (créée automatiquement)
logs/decisions.jsonl   copie lisible du journal des décisions
```

### Un cycle

1. Rafraîchir bougies et prix.
2. Coupe-circuit global : si le book total a perdu plus que le seuil, tout est
   liquidé et plus rien ne repart jamais.
3. Pour chaque cerveau :
   - sorties forcées (stop de perte, objectif de gain), avant toute décision ;
   - construction du dossier : book, positions, indicateurs, décisions passées
     et leur résultat, budget restant ;
   - le cerveau décide ;
   - la couche de risque relit chaque décision, la redimensionne ou la refuse ;
   - exécution des ventes puis des achats ;
   - relevé d'equity.

### Les garde-fous, dans `config.yaml` sous `risk`

| Réglage | Défaut | Effet |
|---|---|---|
| `max_position_pct` | 40 % | part du book maximale sur une position |
| `max_open_positions` | 2 | positions simultanées par cerveau |
| `max_round_trips_per_week` | 3 | nouvelles positions par semaine, le frein anti-frais |
| `max_daily_loss_pct` | 6 % | au-delà, plus d'achats jusqu'au lendemain |
| `kill_switch_drawdown_pct` | 25 % | coupe-circuit définitif sur le book total |
| `stop_loss_pct` / `take_profit_pct` | 8 % / 15 % | posés automatiquement sur chaque position |

## Passage en live

Le live exige **deux serrures** volontairement séparées :

1. `engine.mode: "live"` dans `config.yaml`, ou `--live` en ligne de commande.
2. Un fichier `LIVE_ARMED` à la racine, créé à la main.

Sans le fichier, le programme refuse de démarrer et l'explique. Avant de le
créer :

- créer des clés Binance **sans droit de retrait**, restreintes à votre IP ;
- avoir converti le capital en USDT sur le compte spot ;
- avoir laissé tourner le paper trading assez longtemps pour avoir vu des
  achats, des ventes, un stop et au moins un refus de la couche de risque.

Le testnet Binance (`engine.use_testnet: true`, clés dédiées) permet de valider
le chemin des ordres réels sans argent.

## Ce que l'on ne fait pas ici

- Pas de conseil d'investissement. Le système exécute une expérience, il ne
  recommande rien.
- Pas de manipulation de clés ou d'ordres par l'assistant qui a écrit ce code.
  Les clés sont dans votre `.env`, le fichier `LIVE_ARMED` est le vôtre.
- Pas d'optimisation des paramètres du cerveau `rules` sur le passé : c'est un
  témoin, une courbe de backtest flatteuse le rendrait inutile.
