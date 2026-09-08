# Validation en avant — 900 jours, 5 paires

*8 septembre 2026. Reproductible : `scripts/valider_en_avant.py` puis `scripts/lire_validation.py`.*

## Pourquoi

Vous aviez raison sur un point qui invalidait tout le reste :

> « La méthode des 3 paliers donne d'excellents résultats sur cette courbe parce
> qu'elle est faite pour s'y adapter. Tes résultats sont simplement dus à un
> trop grand focus sur cette période. »

Le banc de robustesse découpait bien le passé de quatre façons — par paire, par
trimestre, par voisinage de réglage, par finesse de bougie — mais **ces quatre
découpages portaient sur les mêmes jours**. Un réglage pouvait les passer tous
en n'étant qu'une bonne adaptation à ce régime de marché précis.

## Le protocole

Historique porté de 400 à **1 000 jours** au pas 5 minutes (remontée jusqu'au
11 décembre 2023, plafond d'OKX). Fenêtre commune à cinq paires — BTC, ETH,
SOL, TRX, UNI — soit **900 jours découpés en 9 blocs de 100 jours**, échelle
remise à neuf au début de chaque bloc. 280 réglages recevables (les autres
poseraient un barreau sous le plancher de 12 €). 12 600 rejeux.

À chaque bloc *k* : on choisit le réglage sur les **3 blocs précédents
seulement**, on relève ce qu'il rend sur le bloc *k*, jamais vu au moment du
choix. Rien dans le critère de choix ne regarde le bloc de test.

## Le verdict

| | par bloc de 100 jours |
|---|---|
| **Choisi dans le passé** (honnête) | **−4,56 %** |
| Ne rien faire, mêmes blocs | +1,59 % |
| **Choisi après coup** (l'illusion) | +0,27 % |
| **Surajustement mesuré** | **−4,82 points** |

4 blocs de test positifs sur 6, 3 battent le repère. Quatre réglages différents
retenus pour six blocs : la sélection suit le bruit.

**Vous aviez raison, et au-delà.** Même le réglage désigné *après coup* ne rend
que +0,27 % par bloc sur 900 jours. Les +16 % publiés venaient d'une fenêtre de
400 jours qui convenait, et d'un panier choisi en connaissant la suite.

## Ce que la sélection rate

Le réglage qui tourne en paper trading, rejoué sur les neuf blocs :

| | b0 | b1 | b2 | b3 | b4 | b5 | b6 | b7 | b8 | moy. | pire |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 paliers (paper) | +0,7 | +1,2 | +2,3 | +2,8 | +2,6 | +3,2 | +0,2 | +0,8 | +2,2 | **+1,76** | **+0,2** |
| 8 paliers (paper) | +1,0 | +4,1 | +6,1 | +2,9 | +3,5 | +2,6 | **−15,5** | +0,5 | +3,5 | +0,98 | −15,5 |
| échelle courte | +4,1 | −8,0 | +6,8 | −23,0 | +13,9 | −5,2 | −31,3 | +2,1 | +10,5 | −3,31 | −31,3 |
| ne rien faire | −8,7 | −6,4 | +59,5 | −29,9 | +37,7 | −3,7 | −40,3 | +6,4 | +39,4 | +6,00 | −40,3 |
| meilleur du bloc, après coup | +9,5 | +10,3 | +27,3 | +9,0 | +23,3 | +9,1 | −4,5 | +12,5 | +25,4 | +13,55 | −4,5 |

Le réglage à 3 paliers est **positif sur les neuf blocs**, pire bloc +0,2 %. Les
blocs b0 à b4 (mars 2024 → août 2025) sont hors de la fenêtre de 400 jours qui
a servi à le choisir : cinq blocs strictement hors échantillon, tous positifs.

La procédure de validation ne le retient jamais, parce qu'elle prend à chaque
fois ce qui a dominé les trois blocs précédents — donc une échelle courte, qui
explose au bloc suivant. **Ce n'est pas l'espace des réglages qui est mauvais,
c'est le fait de choisir sur trois blocs parmi 280 candidats.**

## Ce que la grille est réellement

Pas un moteur de rendement : un **amortisseur**. Elle coupe les deux queues.
Là où détenir perdait 40 %, elle rend +0,2 %. Là où détenir gagnait 59 %, elle
rend +2,3 %. Environ **+6,5 % par an** avec un pire bloc à +0,2 %, contre
~+22 % par an et des blocs à −40 % pour l'achat-conservation.

## Le champ de vision : la réponse

Le moteur ne regarde aucun passé — la référence est le prix du moment, la
profondeur une fraction fixe. Fallait-il lui donner une fenêtre glissante ?

- profondeur gagnante ↔ volatilité du bloc : **+0,20** (rien)
- profondeur gagnante ↔ **chute maximale sous le plus haut** du bloc : **+0,75**
- chute maximale du bloc précédent → celle du bloc suivant : **−0,27**

Le réglage idéal suit bien la profondeur des chutes — mais celles du bloc **qu'on
est en train de vivre**, et rien dans le bloc précédent ne les annonce. Une
profondeur déduite d'une fenêtre glissante se réglerait sur une information qui
ne se prolonge pas : du bruit avec un retard.

**Le champ de vision nul n'est pas un défaut à corriger.** Ce qu'il faut est une
profondeur choisie une fois pour toutes, assez large pour tenir dans le pire
bloc observé (chute de 53 %).

## Ce qui compte vraiment dans les réglages

Rang moyen sur les blocs (plus bas = mieux) :

- **profondeur** : 8 % est nettement le pire (58 %) ; de 15 à 55 %, tout se vaut
- **paliers** : 3 / 5 / 8 → 51 / 49 / 49 %. Aucun effet.
- **ratio** : 1,2 → 3,0 → 51 / 49 / 50 / 50 %. Aucun effet.
- **objectif** : 0,8 % → 60 %, 1,5 % → 53 %, 3 % → 45 %, 6 % → 43 %. **Effet net.**
- **départ sous** : aucun effet.

Deux règles seulement survivent : **ne pas faire l'échelle courte, ne pas viser
un objectif minuscule.** Tout le reste de l'espace des paramètres était du bruit
que les balayages précédents ont pris pour du signal.

## Ce que cette étude ne corrige pas

Les neuf blocs viennent du même marché et de la même décennie. Aucun découpage
ne fabrique un régime qui n'a pas eu lieu. Une validation en avant élimine le
surajustement à une période ; elle ne promet rien sur un futur qui ne
ressemblerait à aucun bloc passé. Neuf points, c'est peu : les corrélations
ci-dessus se lisent comme des ordres de grandeur, pas comme des mesures fines.
