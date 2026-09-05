# Protocole de mesure

Ce document est écrit **avant** le premier trade. Il fixe ce que l'on mesure,
ce qui compterait comme un succès ou un échec, et ce que l'on a le droit de
conclure dans chaque cas. Il ne se modifie plus une fois la fenêtre ouverte.
Le résultat se lit avec ce document à côté, jamais sans.

Il est issu d'un panel de trois propositions indépendantes, statisticien,
trader systématique, méthodologue, notées par deux juges, avec une simulation
Monte Carlo sur les bougies réellement en base. Les chiffres d'ordre de
grandeur viennent de là. Les seuils de verdict, eux, ne sont **jamais** des
constantes : ils se recalculent sur la fenêtre réelle au moment du verdict.

---

## 1. La question, et ce qu'elle peut recevoir comme réponse

La question posée est double : *comment Claude s'organise-t-il pour trader,*
et *obtient-il des résultats positifs ?*

La première a une réponse mesurable avec ce dispositif. La seconde n'en a
qu'une partielle, et il faut l'écrire d'avance pour ne pas se raconter
d'histoires après coup :

- Sur les bougies en base, l'écart-type d'une bougie 4h est de 0,75 % (BTC) à
  1,05 % (ETH, SOL). Cela fait 7 à 9,5 % sur deux semaines, 10 à 13,5 % sur
  quatre. Un avantage réaliste vaut 1 à 2 % par mois. **Le bruit est trois à
  cinq fois plus grand que l'avantage que l'on cherche.**
- Pour qu'un ratio de Sharpe soit significatif sur huit semaines, il faudrait
  qu'il vaille 5. Aucun trader n'y arrive.
- Il faudrait de l'ordre de 100 trades pour détecter un avantage modeste par
  trade, soit 8 à 10 mois à ce rythme, sur un seul régime de marché.

Donc : **une réussite au sens de ce protocole reste un événement qu'un
trader aléatoire sur vingt produit par chance.** Elle s'appelle « signal
compatible avec une compétence », jamais « ça marche ».

Ce que l'expérience mesure bien, en revanche, avec 84 cycles par deux
semaines et 250 décisions par symbole sur huit semaines : la mécanique, le
respect des contraintes, la cohérence du raisonnement, la calibration de la
confiance, et la justesse directionnelle à 24 h.

---

## 2. Le dispositif figé

Ces valeurs sont celles de `config.yaml` au moment de l'ouverture de la
fenêtre. Elles ne bougent plus ensuite. Le hash git du commit d'ouverture est
noté dans l'événement `protocol_start` de la base.

| Élément | Valeur |
|---|---|
| Trader | Claude, `claude-opus-5`, effort `medium`, prompt système figé |
| Capital | 100 USDT, une seule book |
| Marché | Binance spot, BTC/USDT, ETH/USDT, SOL/USDT, bougies 4h |
| Cadence | un cycle de décision toutes les 4 h, chien de garde toutes les 5 min |
| Frais et slippage | 0,10 % et 0,05 % par côté, appliqués à Claude et à tous les repères |
| Position maximale | 40 % du book |
| Positions simultanées | 2 |
| Budget d'ouvertures | 4 par semaine |
| Stop et objectif | −8 % et +15 %, posés à l'achat |
| Perte journalière | 6 % : au-delà, plus d'achats jusqu'au lendemain |
| Coupe-circuit | −20 % du capital, définitif, garde-fou de catastrophe |
| Plafond API | 2,00 $ par jour, protection anti-emballement, jamais atteint en fonctionnement normal |

Le budget de 4 par semaine a été tranché par le panel : pas 3, qui donnerait
n = 15 à 18 trades en huit semaines, pas 5, qui ferait que le plafond souvent
atteint masquerait le comportement de Claude derrière celui du garde-fou. À 4,
l'attendu est de 20 à 24 trades clos en huit semaines, pour un coût en frais
au pire de 1,4 % du capital par mois.

**Une seule modification est autorisée avant t0, aucune après** : passer
`llm.effort` de `medium` à `low` si le coût mesuré pendant le paper dépasse
0,15 $ par appel.

---

## 3. t0 et les fenêtres

**t0** est le premier cycle où Claude a réellement répondu, c'est-à-dire la
première ligne dans la table `api_costs`. Ce n'est ni le premier lancement du
bot, ni le 4 septembre. Le repère buy-and-hold est constitué à ce cycle, aux
prix de ce cycle, et l'événement `protocol_start` est écrit.

**Phase 0, paper, 14 jours à partir de t0.** Elle valide la mécanique et
rien d'autre. Le P&L paper est effacé du rapport final et ne compte dans aucun
repère. Elle autorise le passage en réel si tous les points de la liste de
contrôle (section 8) sont cochés.

**Phase 1, réel, 56 jours minimum à partir d'un nouveau t0**, avec une date
de fin fixée d'avance et écrite dans la base. Le verdict se prononce à cette
date, une seule fois, jamais « au meilleur moment ».

Une **revue intermédiaire à 4 semaines** est autorisée, limitée aux critères
d'échec de sécurité et de processus. Elle ne peut ni déclarer une réussite
ni ajuster quoi que ce soit.

**Fenêtre incomplète.** Si la chaîne de modèle renvoyée par l'API change en
cours de fenêtre, si une panne dépasse 48 h, ou si Binance change ses règles,
la fenêtre est close à la date de l'événement, rapportée telle quelle, et
rien n'est conclu. On ne recolle jamais deux fenêtres pour atteindre le n
minimal.

---

## 4. Les repères

Tous sont en **valeur de liquidation** : ce que rapporterait une vente
immédiate, frais et slippage déduits, exactement comme l'equity de Claude.
Aucun repère ne paie d'API, donc toute comparaison se fait sur la ligne
« trading brut » du bilan (section 5).

**B0, le cash.** Garder les 100 USDT. Rendement 0. C'est le plancher : le
seul résultat que l'on obtient sans rien risquer. Toute activité coûte : un
trader aléatoire perd en médiane 0,7 % sur 14 jours et 2 à 5 % sur 42 jours
par rapport à son jumeau passif, uniquement en frais, slippage et stops
déclenchés par le bruit.

**B1, le panier buy-and-hold.** 100 USDT en trois parts égales à t0,
conservées sans intervention. Calculé automatiquement par le moteur et
relevé à chaque cycle. C'est ce que l'utilisateur aurait eu en ne faisant
rien d'intelligent. **Attention** : Claude est plafonné à 80 % d'exposition et
un trader sous ces règles est exposé 30 à 45 % du temps. En marché haussier il
perdra presque à coup sûr contre B1 sans que cela dise rien de son jugement.
B1 est un repère de contexte, pas un critère de réussite.

**B2, le jumeau à exposition égale.** Ce qu'aurait obtenu quelqu'un qui aurait
détenu le panier B1 avec exactement la même fraction du capital exposée que
Claude, cycle par cycle, sans choisir ni le moment ni la paire. Calcul
chaîné : à chaque cycle, rendement du jumeau = exposition de Claude au cycle
précédent × rendement du panier entre les deux cycles, moins 0,15 % sur chaque
variation d'exposition. **L'écart entre Claude et B2 est la contribution de
son timing et de sa sélection**, séparée du simple fait d'être dans le
marché. C'est la variable la plus proche d'une mesure de compétence que ce
montage permet. Calculé par `scripts/metriques.py`.

**B3, le singe.** 1000 traders fictifs qui, sur le chemin de prix réel de la
fenêtre, achètent et vendent au hasard mais subissent exactement les mêmes
garde-fous, frais et slippage, calibrés pour ouvrir autant de positions que
Claude et les tenir aussi longtemps. Stop et objectif vérifiés sur le low et
le high de chaque bougie. Ils forment la distribution de référence du hasard
pur. On rapporte le **percentile de Claude** dans cette distribution, pour le
rendement brut et pour l'excès face à B2. Calculé par `scripts/singe.py`, à
la fin de la fenêtre, jamais avant, sur la fenêtre exacte.

---

## 5. Le bilan en trois lignes

Toujours affichées ensemble, jamais fusionnées avant le rapport final.

1. **Trading brut** : equity de liquidation moins capital. Frais Binance et
   slippage inclus, API exclu. Seule ligne comparable aux repères.
2. **Coût API** : cumul des appels, avec le nombre d'appels et le coût moyen.
3. **Net tout compris** : ligne 1 moins ligne 2. Ce que l'utilisateur a
   réellement dans la poche.

On ne paie jamais l'API depuis le book de trading. Le « capital de seuil »
au-delà duquel le coût API serait couvert par le rendement ne se calcule pas
depuis une fenêtre de huit semaines : son erreur standard dépasse tout
rendement plausible.

---

## 6. Les verdicts

### Non évaluable financièrement

Le verdict financier n'est **pas prononcé**, et l'on n'écrit ni
« encourageant » ni « décevant », si l'une de ces conditions manque :

- moins de 56 jours de décision effective avec la clé API active ;
- moins de 95 % des cycles exécutés à l'heure ;
- moins de 20 trades clos, ou moins de 15 si Claude a utilisé moins de 60 %
  de son budget d'ouvertures (sa passivité est alors un résultat sur son
  organisation, à documenter comme tel) ;
- un changement de prompt, de modèle servi, de configuration ou de règles
  Binance en cours de fenêtre.

### Échec

Un seul suffit.

- **Coupe-circuit déclenché**, quelle que soit la cause. C'est un échec de
  discipline : il faut de l'ordre de six stops pleins consécutifs pour y
  arriver. Reconstituer la séquence trade par trade.
- **Rendement brut sous le 5ᵉ percentile des singes** de la fenêtre.
- **Excès face à B2 sous le 5ᵉ percentile des singes**.
- **Échec de processus** : plus de 25 % de décisions buy/sell refusées par la
  couche de risque après la deuxième semaine, ou 3 gels journaliers ou plus,
  ou plus de 5 % de cycles sans décision exploitable. Le système n'est alors
  pas en état de mesurer quoi que ce soit, et le résultat financier de la
  période n'est pas rapporté comme verdict.

### Signal positif

Toutes ces conditions à la fois, à la date de fin fixée d'avance :

- rendement brut strictement positif ;
- excès face à B2 **au-dessus du 95ᵉ percentile** des singes de la fenêtre ;
- drawdown maximal inférieur à 15 % ;
- aucun critère d'échec de processus.

Entre le 80ᵉ et le 95ᵉ percentile : « signal faible », rapporté comme tel.

### Zone grise

Tout le reste. C'est l'issue attendue : la probabilité d'y atterrir si Claude
n'a aucune compétence est de 85 à 90 %, et elle reste majoritaire même s'il
en a une réaliste. À écrire en une phrase : *« Le résultat financier est
indistinguable du hasard sous ces règles. »* Puis le rapport porte entièrement
sur les métriques de processus.

**Phrases interdites en zone grise** : « légèrement positif donc
encourageant », « aurait gagné sans les frais », « aurait gagné avec plus de
capital », « meilleur sur la deuxième moitié ». Toute lecture par
sous-période, par paire ou par type de trade est exploratoire et ne peut
nourrir que des hypothèses pour une phase suivante.

---

## 7. Conclusions écrites d'avance, par cas

Le régime de la fenêtre se lit avec chaque verdict : panier au-dessus de
+10 % = haussier, sous −10 % = baissier, sinon range.

**A. Échec financier.** À écrire : *« Sous ces garde-fous, sur cette période
et ce chemin de prix, Claude a fait pire qu'un trader aléatoire. Ce montage,
prompt, dossier, cadence, règles, ne fonctionne pas. »* À ne pas écrire :
« une IA ne peut pas trader ». Interdit : corriger le prompt et relancer sur
la même période, c'est de l'ajustement a posteriori.

**B. Zone grise.** À écrire : *« Le résultat est indistinguable du hasard. Ni
compétence ni incompétence n'est démontrée. Ce que l'expérience a établi,
c'est [taux de refus, cohérence, calibration, justesse directionnelle]. »*

**C. Signal positif.** À écrire : *« Résultat notable, compatible avec une
compétence de timing, non prouvé : un trader aléatoire sur vingt obtient ce
niveau par chance. »* Décision unique autorisée : **réplication** sur une
seconde fenêtre de huit semaines, à l'identique. Deux fenêtres consécutives
au-dessus du seuil (probabilité 1 sur 400 sous le hasard) sont le minimum
avant même de discuter d'un capital supérieur, et seulement d'un capital que
l'on accepte encore de perdre intégralement.

**D. Échec de processus.** À écrire : *« Le système n'est pas en état de
mesurer. »* Retour en paper, correction ciblée d'une seule chose, nouvelle
fenêtre.

**E. Prudence extrême** (moins de 8 trades clos en 8 semaines). À écrire :
*« Aucun verdict financier possible. Constat comportemental : dans ce cadre,
Claude choisit de ne pas trader la plupart du temps ; son résultat est celui
du cash moins les frais plus le coût API. »* C'est un résultat sur son
organisation, pas un échec.

**F. Marché fortement haussier, B1 dépasse Claude de plus de 10 points alors
que Claude est au-dessus de B2.** À écrire : *« La détention passive a fait
mieux parce que le marché a monté et que Claude est plafonné à 80 %
d'exposition, avec une exposition moyenne de X %. À exposition égale, Claude
a fait +Y % de mieux que son jumeau, percentile Z des singes. »* Rapporter
séparément le coût des garde-fous (B1 − B2) et la contribution du timing
(Claude − B2).

**G. Marché baissier, Claude proche de zéro, B1 nettement négatif.** À
écrire : *« Claude a protégé le capital mieux que la détention passive,
essentiellement en restant peu exposé. La part due au timing est l'excès face
à B2, percentile Z ; la part due aux garde-fous et au cash est le reste. »*
Ne pas écrire « Claude a su éviter la baisse ».

**H. Justesse directionnelle supérieure à 60 % mais P&L sous le cash.** À
écrire : *« Le modèle lit le marché mieux que le hasard, mais la conversion en
P&L est cassée. »* Chercher où : frais, taille des positions, stops touchés
sur du bruit.

**I. P&L positif mais justesse directionnelle autour de 50 %.** À écrire :
*« Le gain vient de l'exposition ou de la chance, pas d'une lecture du
marché. »* Ne pas prolonger sur la base du P&L.

**J. Frais supérieurs à 50 % du P&L brut des trades clos.** À écrire :
*« Claude lit peut-être quelque chose, mais il trade trop pour cette taille de
book ; le problème est le turnover, pas l'intelligence. »*

---

## 8. Liste de contrôle de fin de paper

Tous cochés, sans regarder le P&L, avant de créer `LIVE_ARMED` :

- [ ] 84 cycles alignés sur les clôtures 4h, uptime supérieur à 95 %, un
      week-end complet inclus, chien de garde compris
- [ ] au moins un aller-retour complet correctement enregistré : décision,
      ordre, position, frais, equity, dans une seule transaction
- [ ] le chien de garde a fermé au moins une position entre deux cycles
- [ ] au moins un refus de la couche de risque, journalisé avec son motif
- [ ] le compteur de budget hebdomadaire s'est remis à zéro un lundi
- [ ] un redémarrage du bot n'a dupliqué ni position ni cash
- [ ] zéro décision JSON invalide non rattrapée, zéro cycle sans décision
      exploitable pour cause de plafond API
- [ ] coût API mesuré : moyenne par appel notée, effort figé pour la phase 1
- [ ] `scripts/report.py`, `scripts/metriques.py` et `scripts/singe.py`
      tournent de bout en bout sur les données paper
- [ ] alertes reçues sur le téléphone pour un stop et pour un incident simulé
- [ ] sur le testnet : un achat et une vente réels réussis, arrondis de
      quantité et poussière résiduelle observés, coupure réseau simulée sans
      ordre en double
- [ ] capital converti en USDT sur le compte spot ; le montant constaté après
      conversion devient B0 pour la phase 1

Si le P&L paper est très négatif sans cause identifiée, ce n'est pas une
raison de passer en réel « puisque le paper ne prouve rien » : c'est d'abord
un signal de bug à autopsier.

---

## 9. Les métriques de processus

Calculées par `scripts/metriques.py`. Ce sont elles qui répondent à la
question « comment l'IA s'organise ».

**Justesse directionnelle à 24 h.** À chaque cycle, Claude donne un `bias`
(up, down, flat) par symbole, même en hold. Scoré 24 h plus tard avec une
bande neutre de ±0,3 %, une observation par jour et par symbole (cycle de
00:00 UTC) pour éviter les fenêtres qui se chevauchent. C'est **la seule
mesure avec une vraie puissance statistique** : 250 observations en douze
semaines, environ 100 effectives après correction de la corrélation entre les
trois actifs, erreur type 5 points. Hasard à trois classes : environ 33 %.
Seuil prudent : **60 %**. À huit semaines on n'a que 168 observations, la
mesure se lit alors avec son intervalle.

**Taux de refus par la couche de risque**, ventilé par motif, par semaine. Un
taux élevé du même motif signifie que Claude n'intègre pas une contrainte
pourtant rappelée dans chaque dossier. Les holds forcés par le plafond API ne
sont pas des décisions de Claude et sont exclus.

**Calibration de la confiance.** Score de Brier entre la confiance déclarée à
l'achat et l'issue du trade, comparé à 0,25 (confiance constante). Les ventes
forcées, enregistrées avec confiance 1,0, sont exclues.

**Exposition et régime.** Fraction du book investie, part des cycles tout en
hold, croisées avec le régime de la semaine.

**Mode de sortie.** Répartition stop / objectif / signal. Si 90 % des
clôtures viennent de la cage, le jugement de vente n'est jamais exercé. Part
des stops « sur du bruit » : le prix revient au-dessus de l'entrée sous 48 h.

**Usage du budget.** Ouvertures par semaine, jour des ouvertures (tout le
lundi = impatience), taille moyenne en pourcentage du plafond.

**Cohérence des thèses**, à la main : pour chaque position, la thèse d'entrée
est-elle reprise, remplacée ou contredite aux cycles suivants ? Le motif de
vente correspond-il à une condition d'invalidation annoncée à l'entrée ?
Quinze minutes par semaine sur trois décisions tirées avant d'en connaître
l'issue.

---

## 10. Ce que ce protocole ne peut pas faire

Prouver que Claude a une compétence de trading. Même un signal positif reste
un événement qu'un trader aléatoire sur vingt produit par chance. La période
sera dominée par une ou deux jambes de tendance : le résultat mesure d'abord
le régime et la cage, ensuite seulement le modèle. Et Claude est une politique
stochastique observée une seule fois : le même dossier pourrait produire une
autre décision.

Ce qu'il peut faire : établir que la plomberie tient, que le modèle respecte
ou non ses contraintes et apprend ou non de son propre journal, qu'il n'est
pas catastrophiquement mauvais, qu'il lit ou non la direction à 24 h mieux
que le hasard. C'est déjà beaucoup, et c'est la réponse honnête à la question
posée.
