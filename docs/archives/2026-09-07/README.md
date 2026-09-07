# Anciennes simulations, archivees le 7 septembre 2026

Remplacees par la famille d'echelles deduite de la distribution des chutes.
Conservees ici parce qu'un resultat ecarte vaut par la raison qui l'a ecarte :
sans cette trace, la meme piste serait reexploree.

Fenetre : 400 jours, budget 1000 EUR, 
plancher 12 EUR, simulation au pas 5m.
Repere : ne rien faire rendait BTC/EUR -29.5%, ETH/EUR -27.4%, SOL/EUR -33.8%.

| rang | reglage | prof | pal | ratio | objectif | cliquet | gain moyen | epreuves | echoue a |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Cliquet, seuil 4 % | 50% | 3 | x3.3 | 4% | 2%/1.5% | +13.72% | 3/4 | resolutions |
| 2 | Cliquet, seuil 8 % | 50% | 3 | x3.3 | 8% | 4%/1.5% | +12.98% | 3/4 | voisinage |
| 3 | La grille seule, objectif 3 % | 50% | 3 | x2.8 | 3% | — | +11.48% | 4/4 | — |
| 4 | La grille seule | 50% | 3 | x3.3 | 4% | — | +11.33% | 4/4 | — |
| 5 | Patient | 30% | 12 | x1.3 | 3% | — | +9.30% | 4/4 | — |
| 6 | Stop fixe, sans cliquet | 50% | 3 | x3.3 | 4% | 1000%/0.2% | +9.24% | 3/4 | voisinage |
| 7 | Le moins risque | 60% | 5 | x2.4 | 8% | — | +6.66% | 3/4 | voisinage |
| 8 | Le tableau d'origine | 8% | 14 | x1.8 | 2% | — | -1.16% | 1/4 | voisinage, paires, resolutions |

## Ce que ces reglages ont appris

- Le plancher d'ordre de la plateforme decide du ratio utilisable, donc du
  nombre de paliers : moins de paliers autorise une progression plus forte.
- Le vainqueur d'un classement brut sur trois est une coincidence. L'ecart
  entre resolutions de bougies est l'epreuve qui les demasque.
- Le cliquet fonctionne mais son meilleur chiffre est un mirage de cinq
  minutes : +13,7 % en 5 min, +8,7 % en horaire, +10,1 % a la minute.
- Le seuil d'abandon est inerte sur les echelles profondes.

## Pourquoi elles sont remplacees

Ces echelles ont ete choisies par balayage, sans regarder OU le marche
s'arrete reellement de descendre. L'etude des chutes montre que 45 % des
chutes s'arretent a -1 %, et qu'aucune ne depasse -22 % contre sa moyenne
24 h. Une echelle de 50 % de profondeur sur trois paliers laisse donc 25
points de vide entre ses deux premiers barreaux, la ou se produisent la
quasi-totalite des retournements.
