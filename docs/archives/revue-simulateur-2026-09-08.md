# Revue adversariale du simulateur — 8 septembre 2026, INTERROMPUE

Six chasseurs independants ont relu `src/grille.py` en effort maximal. La phase
de refutation (trois verificateurs par anomalie) a ete arretee avant de rendre
son verdict, a la demande de l'utilisateur. **Rien ci-dessous n'est donc
confirme par reproduction.** Ce qui suit est classe par le nombre de chasseurs
qui l'ont trouve independamment, ce qui est le meilleur indice disponible.

## Trouve par six chasseurs sur six — a traiter en premier

### L'abandon est evalue AVANT les achats de la meme bougie
`src/grille.py`, jambe basse de `rejouer()` : le test `l <= d.seuil_abandon`
pose `d.abandonnee = True` **avant** la boucle sur `barreaux_a_poser()`, qui
rend alors une liste vide. Une bougie qui traverse des barreaux libres puis le
seuil d'abandon n'achete donc **aucun** de ces barreaux, alors que le prix les
a bien visites avant d'atteindre le seuil.

Sens du biais : penalise la strategie (des achats reels sont perdus). Ampleur :
a mesurer ; probablement faible sur les echelles profondes ou le seuil n'est
presque jamais atteint, potentiellement notable sur les echelles courtes.

Correctif propose : executer la boucle d'achats d'abord, puis le test
d'abandon. Ou mieux, n'acheter que les barreaux **au-dessus** du seuil dans
cette bougie, puis abandonner.

## Trouve par cinq chasseurs sur six

### `vente_meme_bougie=False` ne protege que le PREMIER achat du lot
`trop_tot` compare `ts` a `d.ouverte_le`, qui est l'horodatage du premier
achat de la descente. Un barreau ajoute plus tard, au bas d'une bougie
haussiere, peut donc etre revendu au haut de la **meme** bougie — exactement
l'aller-retour intra-bougie que l'option pretend interdire.

Sens du biais : flatte la strategie. Ampleur : a mesurer ; c'est le meme
mecanisme qui avait fait passer un reglage de +6,65 % a -0,56 % en changeant
de finesse de bougie, donc potentiellement significatif sur les petits
objectifs.

Correctif propose : garder l'horodatage du **dernier** achat (`dernier_achat_le`)
et tester `ts <= d.dernier_achat_le`.

## Trouve par quatre chasseurs sur six

### Apres une vente, la nouvelle echelle est ancree sur la CLOTURE puis remplie par le BAS de la meme bougie
Sur une bougie baissiere (haut puis bas), la vente a lieu sur la jambe haute,
`Descente(symbole, c, ...)` s'ancre sur la cloture — inconnue a cet instant —
et la jambe basse peut remplir un barreau calcule sur cette cloture.

Mesure faite avant la revue : **1 achat sur plus de 2 000** sur huit
combinaisons. Effet nul en pratique, mais le chemin existe.

Correctif propose : ancrer sur le prix de vente `px`, connu, et laisser
`suivre_hausse` remonter la reference en fin de bougie si la cloture est plus
haute.

## Trouve par deux chasseurs — mineur

- Le pic du drawdown part de `-inf` et non du budget : une perte subie avant la
  premiere cloture est invisible. Initialiser `pic = budget`.
- `Descente.vendre()` ne remet pas `abandonnee` a `False` (la boucle le fait
  juste apres, donc sans effet aujourd'hui, mais fragile).

## Trouve par un chasseur — a verifier ou a ignorer

- Le repere buy-and-hold est valorise sur la derniere bougie **1h** alors que
  la strategie l'est sur la derniere bougie **5m** : les deux fins ne sont pas
  au meme instant (ecart de moins d'une heure).
- La grille 5 min n'est jamais confrontee a la grille 1h dans l'export, et le
  message d'erreur de regularite imprime le mauvais pas.
- Le stop au sondage est servi sans glissement quand la bougie cloture a son
  plus bas (`max(l, c * (1 - glissement))` rend `l` quand `c == l`).
- Etiquettes horaires de la page : date en fuseau local, heure en UTC.
- La docstring de `chercher_reglages.py` promet un score qui « exige de battre
  ne rien faire » ; le score ne le fait pas.
- Le commentaire de l'export dit le seuil d'abandon « jamais atteint » ; il
  l'est onze fois sur les reglages publies.
- La regle d'abandon est presentee comme une protection alors qu'elle n'en
  est pas une sur les echelles profondes (elle est inerte, mesure faite).

## Ce que les chasseurs ont verifie et trouve SAIN

Arithmetique du prix de revient, du prix de sortie et du gain net (frais sur
l'actif recu, objectif net exact). Cash jamais negatif, jamais deux achats du
meme barreau, `remplis` toujours prefixe des barreaux vivants. Egalite des
agregats avec et sans trace. Bougie plate, cle `sorties`, duree de cycle,
horodatage zero : corriges et verrouilles par test.

## Pour reprendre

1. Ecrire un test qui reproduit chacune des deux anomalies serieuses.
2. Corriger, puis re-mesurer les huit reglages publies : si l'ecart depasse
   un point sur l'un d'eux, republier la page et le dire.
3. Le reste dans l'ordre ci-dessus, a la mesure.
