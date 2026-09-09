"""Ce que vaut l'echelle a huit barreaux CONCENTREE, mesure avant de la lancer.

    python scripts/preflight_concentre.py

Le plancher de 12 EUR par ordre interdit une vraie progression geometrique a
huit barreaux tant que le budget est reparti sur cinq paires. La seule facon de
donner a la methode sa forme forte — beaucoup de barreaux ET les grosses mises
en bas — est de concentrer les mille euros. Concentrer supprime la seule
protection gratuite du dispositif, la repartition ; il faut donc savoir ce
qu'on echange avant d'echanger.

Ce script rejoue les trois formes sur les MEMES neuf blocs de cent jours que la
validation en avant, paire par paire, budget entier sur une seule paire :

  A  huit barreaux, raison 1,6   la forme forte, celle que le plancher
                                 n'autorisait pas a 200 EUR
  B  huit barreaux, raison 1,20  meme concentration, mises presque plates :
                                 le temoin qui isole la PROGRESSION
  C  trois barreaux, raison 3,3  le reglage de reference, concentre lui aussi

A moins B mesure ce que la progression apporte. A moins C mesure ce que les huit
barreaux apportent. Sans ces deux temoins, un bon resultat de A ne dirait pas
d'ou il vient.

LE CHOIX DE LA PAIRE NE SE FAIT PAS ICI. Toutes les paires sont affichees, mais
la paire retenue pour le paper trading est decidee sur la LIQUIDITE, connue
d'avance, jamais sur ce tableau : choisir apres coup valait vingt a trente
points d'illusion, deja mesures.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000

FORMES = {
    "A  8 barreaux x1,6  (forme forte)":
        dict(profondeur=0.50, paliers=8, ratio=1.6, objectif_net=0.02, depart_sous=0.02),
    "B  8 barreaux x1,20 (temoin, mises plates)":
        dict(profondeur=0.50, paliers=8, ratio=1.20, objectif_net=0.02, depart_sous=0.02),
    "C  3 barreaux x3,3  (reference concentree)":
        dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04, depart_sous=0.02),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    paires, n = d["paires"], d["n_blocs"]
    t0, bloc_ms, fixe = d["t0"], d["bloc_jours"] * MS_JOUR, d["fixe"]
    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    series = {s: [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
                  cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                             "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                             (s, t0, t0 + n * bloc_ms))] for s in paires}
    cx.close()

    print(f"Budget entier sur UNE paire : {args.budget:.0f} EUR. "
          f"Plancher {args.plancher:.0f} EUR par ordre.")
    print("Echelles, du haut vers le bas :")
    for nom, p in FORMES.items():
        rg = Reglages(frais=0.001, mise_min=args.plancher, **fixe, **p)
        ech = rg.echelle(100.0, args.budget)
        morts = [i for i, (_, e) in enumerate(ech) if e < args.plancher]
        print(f"  {nom}")
        print("      " + "  ".join(f"{e:.0f}" for _, e in ech) + " EUR")
        print("      barreaux de " + ", ".join(f"{pr - 100:+.0f}%" for pr, _ in ech))
        print(f"      sous le plancher : {'AUCUN' if not morts else morts}")
    print()

    ent = "".join(f"{('b' + str(k)):>8}" for k in range(n))
    for nom, p in FORMES.items():
        rg = Reglages(frais=0.001, mise_min=args.plancher, **fixe, **p)
        print("=" * 100)
        print(nom)
        print(f"{'paire':<10}{ent}{'moyenne':>10}{'pire':>9}{'cycles':>8}{'duree moy':>11}")
        moy_par_bloc = [[] for _ in range(n)]
        for s in paires:
            ligne, cyc, dur = [], 0, []
            for k in range(n):
                b = [x for x in series[s]
                     if t0 + k * bloc_ms <= x[0] < t0 + (k + 1) * bloc_ms]
                if len(b) < 100:
                    ligne.append(0.0)
                    continue
                u = resume(rejouer(s, b, rg, args.budget, trace=False))
                ligne.append(u["perf_pct"])
                cyc += u["cycles"]
                if u["cycles"]:
                    dur.append(u["duree_moyenne_h"])
                moy_par_bloc[k].append(u["perf_pct"])
            print(f"{s:<10}" + "".join(f"{v:>+8.1%}" for v in ligne)
                  + f"{sum(ligne)/len(ligne):>+10.2%}{min(ligne):>+9.1%}"
                  + f"{cyc:>8}{(sum(dur)/len(dur) if dur else 0):>10.0f}h")
        mb = [sum(v) / len(v) if v else 0.0 for v in moy_par_bloc]
        print(f"{'moyenne':<10}" + "".join(f"{v:>+8.1%}" for v in mb)
              + f"{sum(mb)/len(mb):>+10.2%}{min(mb):>+9.1%}")
        print("  (la ligne 'moyenne' n'est PAS ce que fera le bot : il ne tiendra qu'UNE")
        print("   paire. Elle sert seulement a comparer les trois formes entre elles.)")
        print()

    for k in range(n):
        print(f"  b{k} = {q(t0 + k * bloc_ms)} -> {q(t0 + (k + 1) * bloc_ms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
