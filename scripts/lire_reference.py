"""Ce que le balayage de la reference a donne, et ce qu'il n'autorise pas a dire.

    python scripts/lire_reference.py

Relit docs/etude-reference.json et applique, dans l'ordre, les epreuves decidees
avant la mesure. Aucun chiffre n'est choisi ici : les regles sont celles ecrites
dans scripts/etudier_reference.py.

LES SIX LECTURES, et ce que chacune interdit de conclure si elle echoue.

  1. LA SURFACE. Ce que chaque combinaison a rendu par bloc, en moyenne sur les
     paires. Descriptive, et rien de plus : le maximum d'une surface calculee sur
     les memes blocs est « choisi apres coup », le chiffre exact que la page de
     validation existe pour discrediter.

  2. LE CHOIX EN AVANT. A chaque bloc, la combinaison retenue sur les trois blocs
     PRECEDENTS, relevee sur le bloc suivant, jamais vu. C'est la seule lecture
     qui mesure ce qu'on obtiendrait vraiment.

  3. LE TEMOIN. Ce que rend une combinaison TIREE AU SORT dans la meme famille.
     C'est lui, et non zero, qu'il faut battre : si choisir ne fait pas mieux que
     tirer au hasard, choisir ne sert a rien. « Ne rien faire » est donne a cote.

  4. LA VALEUR DU CHOIX SELON LA TAILLE DE LA GRILLE. On rejoue le choix en avant
     sur des sous-grilles de K combinaisons tirees au hasard. Si la valeur du
     choix s'annule avant la taille de notre grille, alors notre propre vainqueur
     n'est qu'un tirage chanceux, et il faut le dire.

  5. LES DEUX MOITIES. Le meilleur des blocs 0-4 est-il encore le meilleur des
     blocs 5-8 ? Si l'etoile se promene, c'est du bruit.

  6. LE PLACEBO, et il est dans la grille. N = 1 balaye avec plusieurs departs
     EST « decaler l'echelle d'un montant constant ». Une moyenne ne vaut
     quelque chose que si elle bat le meilleur decalage constant. Sinon la
     trouvaille se reduit a « l'echelle devrait etre un peu plus bas ».
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics as stt
import sys
from collections import defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
MS_JOUR = 86_400_000


def nom(c: dict) -> str:
    n = c["moyenne_ref"]
    if n == 1:
        d = "la cloture"
    elif n < 12:
        d = f"{n * 5} min"
    elif n < 288:
        d = f"{n * 5 / 60:.0f} h".replace(".0", "")
    else:
        d = f"{n * 5 / 1440:.0f} j"
    return f"N={n} ({d}), depart -{c['depart_sous']:.1%}".replace(".0%", "%")


def repere(db: str, paires, t0, n_blocs, bloc_ms, frais) -> dict:
    """Ne rien faire : acheter a l'ouverture du bloc, payer l'aller-retour."""
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[tuple[str, int], float] = {}
    for s in paires:
        for k in range(n_blocs):
            d, f = t0 + k * bloc_ms, t0 + (k + 1) * bloc_ms
            r = cx.execute("SELECT open FROM candles WHERE symbol=? AND timeframe='5m' "
                           "AND ts>=? AND ts<? ORDER BY ts LIMIT 1", (s, d, f)).fetchone()
            q = cx.execute("SELECT close FROM candles WHERE symbol=? AND timeframe='5m' "
                           "AND ts>=? AND ts<? ORDER BY ts DESC LIMIT 1", (s, d, f)).fetchone()
            if r and q and r[0]:
                out[(s, k)] = (q[0] / r[0]) * (1 - frais) ** 2 - 1
    cx.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="docs/etude-reference.json")
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--tirages", type=int, default=4000)
    args = ap.parse_args()

    d = json.loads((RACINE / args.source).read_text(encoding="utf-8"))
    cbs, nb, app = d["combinaisons"], d["n_blocs"], d["apprentissage"]
    bloc_ms = d["bloc_jours"] * MS_JOUR
    paires = d["paires"]

    #  moyenne sur les paires, par (combinaison, bloc) : la regle de choix ecrite
    #  d'avance, identique a celle de la validation en avant.
    par = defaultdict(list)
    detail = defaultdict(list)
    for i, k, s, perf, cyc, gain, blo, eng, dd, ab in d["matrice"]:
        par[(i, k)].append(perf)
        detail[i].append((perf, cyc, eng, dd, gain))
    moy = {ik: sum(v) / len(v) for ik, v in par.items()}
    hold = repere(args.db, paires, d["t0"], nb, bloc_ms, d["frais"])
    hold_bloc = {k: stt.mean([hold[(s, k)] for s in paires if (s, k) in hold])
                 for k in range(nb)}

    print(f"\n{'=' * 78}\n  {len(cbs)} combinaisons · {nb} blocs de {d['bloc_jours']} j · "
          f"{len(paires)} paires · {d['budget']:.0f} EUR\n{'=' * 78}")

    # ------------------------------------------------------------ 1. la surface
    note = {i: stt.mean([moy[(i, k)] for k in range(nb) if (i, k) in moy]) for i in range(len(cbs))}
    pire = {i: min(moy[(i, k)] for k in range(nb) if (i, k) in moy) for i in range(len(cbs))}
    rang = sorted(range(len(cbs)), key=lambda i: -note[i])
    print("\n1. LA SURFACE — descriptive, et rien de plus.")
    print(f"   {'combinaison':<34}{'moy/bloc':>10}{'pire bloc':>11}{'cycles':>8}{'engage':>8}")
    for i in rang[:6] + ["..."] + rang[-3:]:
        if i == "...":
            print("   " + "." * 30)
            continue
        v = detail[i]
        print(f"   {nom(cbs[i]):<34}{note[i]:>9.2%}{pire[i]:>11.1%}"
              f"{stt.mean(x[1] for x in v):>8.0f}{stt.mean(x[2] for x in v):>8.0%}")
    print(f"   amplitude de la surface : {note[rang[0]] - note[rang[-1]]:.2%} par bloc")

    # ------------------------------------------ 2 et 3. le choix en avant, et son temoin
    print(f"\n2-3. LE CHOIX EN AVANT ({app} blocs d'apprentissage), ET SON TEMOIN.")
    print(f"   {'bloc':<6}{'retenu':<34}{'rend':>9}{'tirage au sort':>16}{'ne rien faire':>15}")
    choisis, temoins, holds, apres_coup = [], [], [], []
    for k in range(app, nb):
        appr = list(range(k - app, k))
        i = max(range(len(cbs)),
                key=lambda j: stt.mean([moy[(j, b)] for b in appr if (j, b) in moy]))
        r = moy.get((i, k))
        if r is None:
            continue
        tirage = stt.mean([moy[(j, k)] for j in range(len(cbs)) if (j, k) in moy])
        meilleur = max(moy[(j, k)] for j in range(len(cbs)) if (j, k) in moy)
        choisis.append(r); temoins.append(tirage); holds.append(hold_bloc[k])
        apres_coup.append(meilleur)
        print(f"   b{k:<5}{nom(cbs[i]):<34}{r:>8.2%}{tirage:>16.2%}{hold_bloc[k]:>15.2%}")
    print(f"   {'MOYENNE':<40}{stt.mean(choisis):>8.2%}{stt.mean(temoins):>16.2%}"
          f"{stt.mean(holds):>15.2%}")
    #  UN ECART ENTRE DEUX POURCENTAGES SE COMPTE EN POINTS. Imprimer la fraction
    #  telle quelle affichait « -0,01 point » la ou l'ecart valait trois quarts de
    #  point : cent fois trop petit, et dans le sens rassurant.
    valeur = (stt.mean(choisis) - stt.mean(temoins)) * 100
    print(f"\n   valeur du choix (retenu moins tirage au sort) : {valeur:+.2f} point par bloc")
    print(f"   choisi apres coup : {stt.mean(apres_coup):+.2%} — "
          f"surajustement {(stt.mean(choisis) - stt.mean(apres_coup)) * 100:+.2f} point")

    # --------------------------------------- 4. la valeur du choix selon la taille
    print("\n4. LA VALEUR DU CHOIX SELON LA TAILLE DE LA GRILLE.")
    print(f"   {'K':>5}{'ce qu il promet':>18}{'ce qu il tient':>17}{'gain vs hasard':>17}")
    rng = random.Random(20260914)
    tous = list(range(len(cbs)))
    for K in (2, 4, 8, 16, 32, 64, len(cbs)):
        if K > len(cbs):
            continue
        promis, tenus = [], []
        for _ in range(args.tirages):
            sous = tous if K == len(cbs) else rng.sample(tous, K)
            k = rng.randrange(app, nb)
            appr = list(range(k - app, k))
            i = max(sous, key=lambda j: stt.mean([moy[(j, b)] for b in appr if (j, b) in moy]))
            promis.append(stt.mean([moy[(i, b)] for b in appr if (i, b) in moy]))
            if (i, k) in moy:
                tenus.append((moy[(i, k)], stt.mean([moy[(j, k)] for j in sous if (j, k) in moy])))
        g = stt.mean(a - b for a, b in tenus) * 100
        print(f"   {K:>5}{stt.mean(promis):>17.2%}{stt.mean(a for a, _ in tenus):>17.2%}"
              f"{g:>+16.2f} pt")

    # ------------------------------------------------------- 5. les deux moities
    print("\n5. LES DEUX MOITIES.")
    m1 = list(range(0, nb // 2)); m2 = list(range(nb // 2, nb))
    n1 = {i: stt.mean([moy[(i, k)] for k in m1 if (i, k) in moy]) for i in range(len(cbs))}
    n2 = {i: stt.mean([moy[(i, k)] for k in m2 if (i, k) in moy]) for i in range(len(cbs))}
    b1 = max(n1, key=n1.get); b2 = max(n2, key=n2.get)
    r1 = sorted(range(len(cbs)), key=lambda i: -n2[i]).index(b1) + 1
    print(f"   meilleur des blocs {m1[0]}-{m1[-1]} : {nom(cbs[b1])}")
    print(f"   meilleur des blocs {m2[0]}-{m2[-1]} : {nom(cbs[b2])}")
    print(f"   le champion de la premiere moitie finit {r1}e sur {len(cbs)} dans la seconde")
    ordre = stt.correlation([n1[i] for i in range(len(cbs))], [n2[i] for i in range(len(cbs))])
    print(f"   correlation des deux classements : {ordre:+.2f}")

    # ------------------------------------------------------------- 6. le placebo
    print("\n6. LE PLACEBO — une moyenne bat-elle un simple decalage ?")
    plats = [i for i, c in enumerate(cbs) if c["moyenne_ref"] == 1]
    moyennes = [i for i, c in enumerate(cbs) if c["moyenne_ref"] > 1]
    bp = max(plats, key=lambda i: note[i]); bm = max(moyennes, key=lambda i: note[i])
    print(f"   meilleur sans moyenne : {nom(cbs[bp]):<34}{note[bp]:>8.2%}")
    print(f"   meilleur avec moyenne : {nom(cbs[bm]):<34}{note[bm]:>8.2%}")
    print(f"   ce que la moyenne ajoute, au mieux et apres coup : "
          f"{(note[bm] - note[bp]) * 100:+.2f} point par bloc")

    # ------------------------------------------------------- 7. les deux axes
    print("\n7. LES DEUX AXES, SEPAREMENT — ce que chacun commande vraiment.")
    eng_moy = {i: stt.mean(x[2] for x in detail[i]) for i in range(len(cbs))}
    print(f"   {'N':>6}{'moy/bloc':>11}{'pire bloc':>11}{'engage':>9}     "
          f"{'depart':>7}{'moy/bloc':>11}{'pire bloc':>11}{'engage':>9}")
    axeN, axeD = [], []
    for n in d["n_bougies"]:
        idx = [i for i, c in enumerate(cbs) if c["moyenne_ref"] == n]
        axeN.append((n, stt.mean(note[i] for i in idx), stt.mean(pire[i] for i in idx),
                     stt.mean(eng_moy[i] for i in idx)))
    for ds in d["departs"]:
        idx = [i for i, c in enumerate(cbs) if abs(c["depart_sous"] - ds) < 1e-9]
        axeD.append((ds, stt.mean(note[i] for i in idx), stt.mean(pire[i] for i in idx),
                     stt.mean(eng_moy[i] for i in idx)))
    for j in range(max(len(axeN), len(axeD))):
        g = (f"   {axeN[j][0]:>6}{axeN[j][1]:>10.2%}{axeN[j][2]:>11.1%}{axeN[j][3]:>9.0%}"
             if j < len(axeN) else " " * 37)
        dd_ = (f"     {axeD[j][0]:>6.1%}{axeD[j][1]:>10.2%}{axeD[j][2]:>11.1%}{axeD[j][3]:>9.0%}"
               if j < len(axeD) else "")
        print(g + dd_)
    print(f"   amplitude de l'axe N : {max(x[1] for x in axeN) - min(x[1] for x in axeN):.2%}"
          f" · de l'axe depart : {max(x[1] for x in axeD) - min(x[1] for x in axeD):.2%}")

    # ------------------------------------------------- 8. a exposition egale
    print("\n8. A EXPOSITION EGALE — le test decisif.")
    print("   Une moyenne longue engage moins de capital, donc elle creuse moins. Reste")
    print("   a savoir si elle fait mieux qu'un simple ecart qui engage AUTANT.")
    plats2 = [i for i, c in enumerate(cbs) if c["moyenne_ref"] == 1]
    ecarts = []
    for i in range(len(cbs)):
        if cbs[i]["moyenne_ref"] == 1:
            continue
        j = min(plats2, key=lambda k: abs(eng_moy[k] - eng_moy[i]))
        if abs(eng_moy[j] - eng_moy[i]) < 0.06:
            ecarts.append(note[i] - note[j])
    if len(ecarts) > 2:
        m = stt.mean(ecarts) * 100
        sd = stt.stdev(ecarts) * 100
        t = m / (sd / len(ecarts) ** 0.5)
        print(f"   {len(ecarts)} couples apparies a moins de six points d'engagement")
        print(f"   ce que la moyenne ajoute : {m:+.2f} point par bloc "
              f"(ecart-type {sd:.2f}, t = {t:+.2f})")
        print(f"   -> {'distinguable de zero' if abs(t) > 2 else 'INDISTINGUABLE DE ZERO'}")

    # -------------------------------------------------------- l'artefact mesure
    print("\n   POUR MEMOIRE — a quel point la reference decroche du prix :")
    for n in d["n_bougies"]:
        a = [d["artefact"][s][str(n)] for s in paires if s in d["artefact"]]
        if not a:
            continue
        print(f"     N={n:>5} : au-dessus du prix {stt.mean(x['au_dessus'] for x in a):>4.0%} "
              f"du temps, de plus de 2 % {stt.mean(x['au_dessus_2pct'] for x in a):>5.1%}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
