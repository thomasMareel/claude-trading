"""Relit la matrice de la validation en avant et repond a trois questions.

    python scripts/lire_validation.py

Le calcul lourd a deja eu lieu : valider_en_avant.py a mesure chaque reglage
sur chaque bloc et ecrit le resultat. Ce script ne rejoue que ce qui manque —
les reglages nommes en dur, dont celui qui tourne en paper trading — et lit
tout le reste dans le fichier.

  1. QUE VAUT LE REGLAGE EN COURS, bloc par bloc, face au repere et face au
     meilleur reglage que le passe aurait designe.
  2. LE REGLAGE OPTIMAL SUIT-IL LA VOLATILITE ? Si la profondeur qui gagne
     dans un bloc calme n'est pas celle qui gagne dans un bloc agite, alors
     un bot qui REGARDE les N derniers jours a quelque chose a y gagner. Si
     elle ne suit rien, le champ de vision est une fausse piste et la
     profondeur fixe est le bon choix.
  3. QUELLE FAMILLE SURVIT PARTOUT, une fois qu'on refuse de choisir apres
     coup : on regarde le rang moyen de chaque valeur de chaque parametre,
     bloc par bloc, ce qui ne depend pas d'un gagnant unique.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000

#  Les reglages qu'on veut voir nommement, quoi qu'en dise la grille. Le premier
#  est celui qui tourne en paper trading depuis le 8 septembre 2026 : c'est LUI
#  que la validation doit juger, pas un cousin arrondi aux valeurs de la grille.
NOMMES = {
    "paper trading en cours": dict(profondeur=0.50, paliers=3, ratio=3.3,
                                   objectif_net=0.04, depart_sous=0.02),
    "8 paliers (paper trading aussi)": dict(profondeur=0.50, paliers=8, ratio=1.20,
                                            objectif_net=0.02, depart_sous=0.02),
    "echelle courte et frequente": dict(profondeur=0.12, paliers=4, ratio=1.8,
                                        objectif_net=0.015, depart_sous=0.0),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--db", default="data/trading.db")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    cbs, paires, n = d["reglages"], d["paires"], d["n_blocs"]
    t0, bloc_ms = d["t0"], d["bloc_jours"] * MS_JOUR
    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731

    par = defaultdict(list)
    for i, k, s, perf, cyc, dur, blq, dd, eng in d["matrice"]:
        par[(i, k)].append(perf)
    moy = {ik: sum(v) / len(v) for ik, v in par.items()}
    hold = defaultdict(list)
    for s, k, v in d["hold"]:
        hold[k].append(v)
    hold_b = {k: sum(v) / len(v) for k, v in hold.items()}

    #  --- volatilite realisee par bloc, pour la question du champ de vision ---
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    series = {}
    for s in paires:
        series[s] = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
                     cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? "
                                "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                                (s, t0, t0 + n * bloc_ms))]
    cx.close()
    vol, amp = {}, {}
    for k in range(n):
        vv, aa = [], []
        for s in paires:
            b = [x for x in series[s] if t0 + k * bloc_ms <= x[0] < t0 + (k + 1) * bloc_ms]
            if len(b) < 100:
                continue
            r = [b[j][4] / b[j - 1][4] - 1 for j in range(1, len(b)) if b[j - 1][4] > 0]
            vv.append(stt.pstdev(r) * (288 ** 0.5))          # volatilite journaliere
            #  amplitude : de combien le prix descend sous son plus haut glissant.
            #  C'est cela que l'echelle doit couvrir, bien plus que l'ecart-type.
            haut, pire = b[0][2], 0.0
            for x in b:
                haut = max(haut, x[2])
                pire = min(pire, x[3] / haut - 1)
            aa.append(-pire)
        vol[k] = sum(vv) / len(vv)
        amp[k] = sum(aa) / len(aa)

    #  --- 1. les reglages nommes, rejoues sur les memes blocs ---
    print("=" * 92)
    print("LES REGLAGES NOMMES, BLOC PAR BLOC (memes blocs, meme panier, echelle neuve)")
    print("=" * 92)
    resultats = {}
    for nom, p in NOMMES.items():
        rg = Reglages(frais=0.001, mise_min=d["plancher"], **d["fixe"], **p)
        ligne = []
        for k in range(n):
            vals = []
            for s in paires:
                b = [x for x in series[s]
                     if t0 + k * bloc_ms <= x[0] < t0 + (k + 1) * bloc_ms]
                if len(b) < 100:
                    continue
                vals.append(resume(rejouer(s, b, rg, d["budget"], trace=False))["perf_pct"])
            ligne.append(sum(vals) / len(vals) if vals else 0.0)
        resultats[nom] = ligne

    entete = "".join(f"{('b' + str(k)):>9}" for k in range(n))
    print(f"{'':<36}{entete}{'moyenne':>11}{'pire':>9}")
    for nom, ligne in resultats.items():
        cells = "".join(f"{v:>+9.1%}" for v in ligne)
        print(f"{nom:<36}{cells}{sum(ligne)/len(ligne):>+11.2%}{min(ligne):>+9.1%}")
    hl = [hold_b[k] for k in range(n)]
    print(f"{'ne rien faire (repere)':<36}"
          + "".join(f"{v:>+9.1%}" for v in hl)
          + f"{sum(hl)/len(hl):>+11.2%}{min(hl):>+9.1%}")
    mb = [max(moy[(i, k)] for i in range(len(cbs)) if (i, k) in moy) for k in range(n)]
    print(f"{'le meilleur du bloc (apres coup)':<36}"
          + "".join(f"{v:>+9.1%}" for v in mb)
          + f"{sum(mb)/len(mb):>+11.2%}{min(mb):>+9.1%}")
    print()
    print("    " + "".join(f"{('b' + str(k)):>9}" for k in range(n)))
    print(f"{'volatilite jour.':<36}" + "".join(f"{vol[k]:>9.1%}" for k in range(n)))
    print(f"{'plus forte chute sous le haut':<36}" + "".join(f"{amp[k]:>9.0%}" for k in range(n)))
    for k in range(n):
        print(f"    b{k} = {q(t0 + k * bloc_ms)} -> {q(t0 + (k + 1) * bloc_ms)}")

    #  --- 2. le champ de vision : la profondeur gagnante suit-elle le marche ? ---
    print()
    print("=" * 92)
    print("LE CHAMP DE VISION A-T-IL UN SENS ?")
    print("=" * 92)
    print("Le moteur ne regarde AUCUN passe : la reference est le prix du moment et la")
    print("profondeur est une fraction fixe. Lui donner un champ de vision n'aurait")
    print("d'interet que si la profondeur gagnante changeait avec le marche. Voici, bloc")
    print("par bloc, la profondeur des dix meilleurs reglages, face a ce que le marche a")
    print("reellement fait.")
    print()
    print(f"{'bloc':<6}{'volatilite':>12}{'chute max':>11}{'prof. mediane du top 10':>26}"
          f"{'paliers':>9}{'objectif':>10}")
    prof_top, vols, amps = [], [], []
    for k in range(n):
        cl = sorted((i for i in range(len(cbs)) if (i, k) in moy),
                    key=lambda i: moy[(i, k)], reverse=True)[:10]
        if not cl:
            continue
        pr = stt.median(cbs[i]["profondeur"] for i in cl)
        pa = stt.median(cbs[i]["paliers"] for i in cl)
        ob = stt.median(cbs[i]["objectif_net"] for i in cl)
        prof_top.append(pr)
        vols.append(vol[k])
        amps.append(amp[k])
        print(f"b{k:<5}{vol[k]:>12.1%}{amp[k]:>11.0%}{pr:>26.0%}{pa:>9.0f}{ob:>10.1%}")

    def correl(x, y):
        if len(x) < 3:
            return 0.0
        mx, my = sum(x) / len(x), sum(y) / len(y)
        num = sum((a - mx) * (b - my) for a, b in zip(x, y))
        den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
        return num / den if den else 0.0

    cv, ca = correl(vols, prof_top), correl(amps, prof_top)
    print()
    print(f"  correlation profondeur gagnante / volatilite du bloc : {cv:+.2f}")
    print(f"  correlation profondeur gagnante / chute max du bloc  : {ca:+.2f}")

    #  Le lien ci-dessus se lit sur le bloc COURANT, que le bot ne connait pas
    #  encore. Un champ de vision ne sert que si le passe annonce l'avenir : on
    #  mesure donc si la chute maximale d'un bloc annonce celle du suivant. Sans
    #  cette seconde mesure, la premiere ferait conclure a tort.
    suite = [amp[k] for k in range(n)]
    ac = correl(suite[:-1], suite[1:])
    print(f"  correlation chute du bloc precedent -> chute du bloc suivant : {ac:+.2f}")
    print()
    if abs(ca) >= 0.5 and abs(ac) < 0.4:
        print("  Le reglage ideal SUIT bien la profondeur des chutes — mais celles du bloc")
        print("  qu'on est en train de vivre, et rien dans le bloc precedent ne les annonce.")
        print("  Une profondeur deduite d'une fenetre glissante se reglerait donc sur une")
        print("  information qui ne se prolonge pas : c'est du bruit avec un retard. Le")
        print("  champ de vision nul du moteur — reference = prix du moment, profondeur")
        print("  fixe — n'est pas un defaut a corriger. Ce qu'il faut, c'est une profondeur")
        print("  choisie assez large pour tenir dans le PIRE bloc observe, une fois pour")
        print("  toutes.")
    elif abs(ca) >= 0.5:
        print("  Le reglage ideal suit la profondeur des chutes, ET le bloc precedent")
        print("  annonce en partie le suivant : une profondeur deduite d'une fenetre")
        print("  glissante merite d'etre testee — EN AVANT, jamais sur le passe ajuste.")
    else:
        print("  Aucun lien exploitable : mesurer les N derniers jours pour en deduire la")
        print("  profondeur reviendrait a suivre du bruit. Le champ de vision nul du")
        print("  moteur actuel n'est donc pas un defaut a corriger.")

    #  --- 3. quelle valeur de chaque parametre tient, sans choisir de gagnant ---
    print()
    print("=" * 92)
    print("CE QUI TIENT, PARAMETRE PAR PARAMETRE (rang moyen sur les blocs, 1 = le mieux)")
    print("=" * 92)
    for cle in ("profondeur", "paliers", "ratio", "objectif_net", "depart_sous"):
        rangs = defaultdict(list)
        for k in range(n):
            cl = sorted((i for i in range(len(cbs)) if (i, k) in moy),
                        key=lambda i: moy[(i, k)], reverse=True)
            tot = len(cl)
            for r, i in enumerate(cl, 1):
                rangs[cbs[i][cle]].append(r / tot)      # rang relatif, 0 = tete
        print(f"  {cle}")
        for v in sorted(rangs):
            m = sum(rangs[v]) / len(rangs[v])
            barre = "#" * round((1 - m) * 40)
            print(f"      {v:<8} rang moyen {m:.0%}  {barre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
