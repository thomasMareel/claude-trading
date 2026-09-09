"""Pourquoi les barreaux du bas ne sont jamais achetes, et quel reglage y remedie.

    python scripts/etudier_reancrage.py

LE DEFAUT, tel que la revue l'a etabli. Avec suivre_hausse=True et
reancrage_min=0, l'echelle se recolle au prix a CHAQUE cloture haussiere tant
que rien n'est engage. Le premier barreau reste donc a depart_sous du plus haut
courant, et le prix n'a jamais le temps de descendre vers les barreaux profonds
avant que l'echelle ne remonte avec lui. Sur BTC/EUR, mille jours, echelle a
huit barreaux de raison 1,6 : b7 achete ZERO fois, b6+b7 = 62 % du budget
quasiment jamais engages, 6,6 % du capital au travail en moyenne. Ce qui produit
le rendement, c'est le premier barreau a 14 EUR ; la "forme forte" est une
figure de style.

DEUX BOUTONS PEUVENT LE CORRIGER, et ils ne font pas la meme chose :

  reancrage_min   de combien le prix doit MONTER avant que l'echelle ne se
                  redeplace. Rend l'ancrage collant, donc une baisse durable
                  descend vraiment l'echelle au lieu de la voir fuir vers le
                  haut. Ne change pas la profondeur des barreaux.

  profondeur      jusqu'ou l'echelle descend. La reduire rapproche les barreaux
                  du bas de la zone ou le prix va reellement, au prix de la
                  protection contre le pire.

LA REGLE DE CHOIX EST ECRITE ICI, AVANT DE VOIR LES RENDEMENTS, et c'est tout
l'objet de ce fichier : on retient le couple qui met la plus grande part du
capital AU TRAVAIL (engage_moyen), sous la contrainte que le dernier barreau
couvre encore la pire chute observee sur la fenetre pour la paire retenue.
Cette contrainte est celle que vous avez posee — "le dernier achat doit se faire
a la valeur la plus basse enregistree, pour prevoir le pire". Le rendement est
affiche, mais il ne participe pas au choix : choisir dessus serait refaire
exactement l'erreur que la validation en avant a chiffree a 4,82 points.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000
_SER: dict = {}

REANCRAGES = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12)
PROFONDEURS = (0.50, 0.40, 0.30, 0.22)


def pire_chute(b: list[tuple]) -> float:
    """De combien le prix est descendu sous son plus haut glissant, au pire."""
    haut, pire = b[0][2], 0.0
    for x in b:
        haut = max(haut, x[2])
        pire = min(pire, x[3] / haut - 1)
    return -pire


def _init(db: str, paires: list[str], t0: int, t1: int):
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for s in paires:
        _SER[s] = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
                   cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                              "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (s, t0, t1))]
    cx.close()


def _tache(job):
    prof, rea, budget, plancher, fixe = job
    p = dict(profondeur=prof, paliers=8, ratio=1.6, objectif_net=0.02, depart_sous=0.02,
             reancrage_min=rea)
    rg = Reglages(frais=0.001, mise_min=plancher, **fixe, **p)
    ech = rg.echelle(1.0, budget)
    out = []
    for s, b in _SER.items():
        r = rejouer(s, b, rg, budget, trace=True)
        u = resume(r)
        #  combien de fois chaque barreau a ete achete : c'est LA mesure qui
        #  dit si l'echelle existe vraiment ou seulement sur le papier.
        coups = [0] * len(ech)
        mises = [m for _, m in ech]
        for e in r["journal"]:
            if e.genre != "achat":
                continue
            #  on retrouve le barreau par sa mise : dans une progression
            #  geometrique stricte, deux barreaux n'ont jamais la meme.
            i = min(range(len(mises)), key=lambda k: abs(mises[k] - e.euros))
            coups[i] += 1
        out.append(dict(paire=s, prof=prof, rea=rea, perf=u["perf_pct"],
                        engage=u["engage_moyen"], creux=u["drawdown_max"],
                        cycles=u["cycles"], duree=u["duree_moyenne_h"], coups=coups,
                        bas=ech[-1][0] - 1.0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--paire-retenue", default="BTC/EUR")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--sortie", default="docs/etude-reancrage.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    paires, n = d["paires"], d["n_blocs"]
    t0, t1 = d["t0"], d["t0"] + n * d["bloc_jours"] * MS_JOUR
    fixe = d["fixe"]

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    ser = {s: [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
               cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                          "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (s, t0, t1))]
           for s in paires}
    cx.close()
    chutes = {s: pire_chute(b) for s, b in ser.items()}
    exigee = chutes[args.paire_retenue]
    print(f"Pire chute sous le plus haut glissant, sur les {n * d['bloc_jours']} jours :")
    for s in paires:
        marque = "   <- paire retenue (liquidite)" if s == args.paire_retenue else ""
        print(f"  {s:<10} {chutes[s]:.1%}{marque}")
    print(f"\nCONTRAINTE POSEE D'AVANCE : le dernier barreau doit descendre au moins")
    print(f"a -{exigee:.0%} pour couvrir le pire de {args.paire_retenue}.")
    print(f"CRITERE DE CHOIX : parmi les couples qui la respectent, celui qui met la")
    print(f"plus grande part du capital au travail. Le rendement ne choisit pas.\n")

    jobs = [(pr, re, args.budget, args.plancher, fixe)
            for pr in PROFONDEURS for re in REANCRAGES]
    lignes = []
    with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                             initargs=(args.db, paires, t0, t1)) as ex:
        for lot in ex.map(_tache, jobs):
            lignes.extend(lot)
    Path(args.sortie).write_text(json.dumps(lignes, separators=(",", ":")), encoding="utf-8")

    ret = [x for x in lignes if x["paire"] == args.paire_retenue]
    ret.sort(key=lambda x: (-x["prof"], x["rea"]))
    print("=" * 104)
    print(f"{args.paire_retenue} — echelle a 8 barreaux, raison 1,6, {args.budget:.0f} EUR, "
          f"{n * d['bloc_jours']} jours en continu")
    print("=" * 104)
    print(f"{'prof.':>6}{'bas':>7}{'reancrage':>11}{'capital au travail':>20}"
          f"{'barreaux jamais achetes':>26}{'rendement':>11}{'creux':>9}{'cycles':>8}")
    for x in ret:
        morts = [i for i, c in enumerate(x["coups"]) if c == 0]
        ok = "" if x["bas"] <= -exigee + 1e-9 else "  (couvre pas le pire)"
        print(f"{x['prof']:>6.0%}{x['bas']:>7.0%}{x['rea']:>11.0%}{x['engage']:>19.1%}"
              f"{(str(morts) if morts else 'aucun'):>26}{x['perf']:>+11.1%}"
              f"{x['creux']:>+9.1%}{x['cycles']:>8}{ok}")

    eligibles = [x for x in ret if x["bas"] <= -exigee + 1e-9]
    if not eligibles:
        print("\nAucun couple ne couvre la pire chute : la contrainte est incompatible")
        print("avec huit barreaux et le plancher de 12 EUR.")
        return 1
    choisi = max(eligibles, key=lambda x: x["engage"])
    morts = [i for i, c in enumerate(choisi["coups"]) if c == 0]
    print("\n" + "=" * 104)
    print("COUPLE RETENU PAR LA REGLE (pas par le rendement)")
    print("=" * 104)
    print(f"  profondeur {choisi['prof']:.0%}, dernier barreau a {choisi['bas']:.0%}, "
          f"reancrage_min {choisi['rea']:.0%}")
    print(f"  capital au travail : {choisi['engage']:.1%} du budget "
          f"(contre {ret[0]['engage']:.1%} avec le reglage refuse)")
    print(f"  barreaux jamais achetes : {morts if morts else 'aucun'}")
    print(f"  ce que cela rend, pour information seulement : {choisi['perf']:+.1%} sur la "
          f"fenetre, creux {choisi['creux']:+.1%}, {choisi['cycles']} cycles")
    print("\n  Sur les autres paires, le MEME couple, sans re-choisir :")
    for s in paires:
        x = next((y for y in lignes if y["paire"] == s and y["prof"] == choisi["prof"]
                  and y["rea"] == choisi["rea"]), None)
        if x:
            print(f"    {s:<10} capital au travail {x['engage']:>5.1%}, "
                  f"rendement {x['perf']:>+7.1%}, creux {x['creux']:>+6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
