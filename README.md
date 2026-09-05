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

**Tout se lit depuis une seule page** : [thomasmareel.github.io/claude-trading](https://thomasmareel.github.io/claude-trading/).
Le bot y publie ses relevés après chaque cycle. Voir « L'interface » plus bas.

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

## L'interface

Une page statique, hébergée par GitHub Pages depuis le dossier `docs/`, lit
des fichiers JSON que le bot exporte et pousse après chaque cycle :
état du bot, tuiles, courbe d'equity de Claude face au repère (toutes deux en
valeur de liquidation), exposition, positions, journal des décisions avec le
raisonnement et les refus, métriques et critères du protocole (explicitement
**pas un verdict**), catalogue des mandats, fenêtres passées, événements.

- **Le dépôt est public.** Tout ce qui est exporté est visible par tous.
  L'export ne sort que des champs nommés (liste blanche) et nettoie toute
  valeur de secret présente dans l'environnement ainsi que les motifs de
  clés connus. Aucun secret n'a jamais été commité.
- La page **ne pilote rien**. Les clés restent sur le PC, aucun ordre ne part
  d'elle. Elle affiche des relevés vieux au plus d'un cycle.
- Forcer une publication à la main : `python scripts/publier.py`.
- Deux boutons en bas de page : **Ouvrir Claude Code** et **Demander à Claude**,
  qui ouvre une demande GitHub pré-remplie (voir « Le pont »).

## Les mandats

Un mandat est la façon dont Claude est missionné : un brief injecté dans son
prompt système, un profil de risque dans des bornes fixes, un univers. Une
fiche par mandat dans `strategies/<id>.yaml`. La fiche est à la fois la
documentation que vous lisez sur la page et la configuration exacte que Claude
reçoit : ce qui est écrit est ce qui est envoyé.

Le catalogue vient d'un panel de trois propositions notées par deux juges,
puis d'une passe de vérification sur la faisabilité de chaque règle avec les
seuls champs du dossier, le chevauchement entre fiches et l'absence de
promesse. Les fiches s'organisent sur trois axes : l'horizon de détention,
le rapport au mouvement, le tempérament, défini arithmétiquement par la perte
sur le book quand un trade tourne mal. Chaque fiche dit honnêtement si un
verdict financier est probable, et « quand ça casse » avant « quand ça
marche ».

**Un mandat se choisit avant t0 et ne change plus pendant la fenêtre.** Le
brief exact est pré-enregistré à t0 ; toute retouche en cours de fenêtre est
signalée par `scripts/metriques.py` et par la page. Le mandat témoin `libre`
est obligatoire : Claude y choisit lui-même sa méthode.

Changer de mandat, c'est ouvrir une nouvelle fenêtre :

```bash
python scripts/fenetre.py                              # état et mandats disponibles
python scripts/fenetre.py --clore --mandat tendance --yes
```

La fenêtre courante est archivée dans `docs/data/fenetres.json` (bilan,
repères, processus, justesse à 24 h), le mandat est écrit dans `config.yaml`,
l'expérience est remise à zéro, et il faut redémarrer le bot. Refusé en mode
réel.

## Le pont : demander quelque chose à Claude depuis la page

Le bouton **Demander à Claude** ouvre une demande GitHub (label `demande`).
Le script `scripts\pont.bat` la traite avec Claude Code en mode non
interactif, dans une copie de travail git séparée du bot : Claude lit la
demande, vérifie dans le code et les relevés, répond dans le fil, et si une
modification est nécessaire l'ouvre en pull request à valider. Il ne passe
jamais d'ordre, ne touche jamais aux clés, et toute modification de
configuration ou de mandat est proposée **pour la fenêtre suivante**.

Prérequis : `gh auth status` connecté, et Claude Code connecté dans un
terminal (`claude`, une fois). Lancer le pont à la main, ou le planifier
(Planificateur de tâches Windows, une fois par heure). Alternative sans PC :
une routine Claude dans le cloud, qui exige d'installer l'application GitHub
de Claude sur le dépôt.

## Architecture

```
config.yaml            tous les réglages, dont le mandat actif (experiment.mandate)
docs/index.html        l'interface, servie par GitHub Pages
docs/data/*.json       les relevés publiés par le bot (sans aucun secret)
docs/protocole.md      ce que l'on mesure, ce que l'on peut conclure
docs/cles-api.md       créer les clés, pas à pas
strategies/*.yaml      les mandats : documentation et configuration en un seul fichier
src/
  config.py            chargement et validation de la config, application du mandat, secrets (env uniquement)
  mandates.py          chargement et bornes des mandats, brief injecté dans le prompt
  metrics.py           les métriques du protocole, un seul calcul pour le terminal et la page
  export.py            export des relevés en JSON, liste blanche et nettoyage des secrets
  publish.py           add, commit, push des relevés, jamais d'exception vers le trading
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
scripts/               points d'entrée : boucle, cycle, journal, rapport, métriques, singe,
                       fenêtre, publication, pont, acquittement, vérification des clés
tests/                 risque, moteur, cerveau, indicateurs, config, ordres, mandats, métriques, export
data/trading.db        la base, seule source de vérité
logs/decisions.jsonl   miroir lisible des décisions (pas un journal de secours)
logs/loop.log          sortie de la boucle détachée
```

### Un cycle

1. Rafraîchir bougies et prix. Les indicateurs sont calculés sur les bougies
   **clôturées** uniquement.
2. Coupe-circuit : si le book a perdu plus que le seuil, tout est liquidé et
   plus rien ne repart jamais. C'est un garde-fou de catastrophe, pas un stop.
3. Sorties forcées : stop de perte, objectif de gain.
4. Construction du dossier : book, positions, indicateurs, décisions passées
   et leur résultat, budget restant.
5. Claude décide.
6. La couche de risque relit chaque décision, la redimensionne ou la refuse.
7. Exécution des ventes puis des achats. Ordre et position sont écrits dans
   une seule transaction ; si l'écriture échoue après un remplissage réel, le
   book est déclaré incertain.
8. Relevé d'equity de Claude, **en valeur de liquidation** : frais et slippage
   de sortie déduits, exactement comme le repère.
9. Relevé du repère buy-and-hold. Il est constitué au premier cycle où Claude
   a **vraiment répondu** (t0 du protocole, première ligne dans `api_costs`),
   aux prix de ce cycle : capital divisé à parts égales entre les paires,
   frais et slippage d'entrée déduits. Tant que la clé API manque, il n'y a
   pas de repère, et le journal le dit.

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
