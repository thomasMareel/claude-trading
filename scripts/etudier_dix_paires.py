"""Ce que l'echelle a huit barreaux donne, paire par paire, sur 900 jours.

    python scripts/etudier_dix_paires.py

Le paper trading a huit barreaux tourne desormais sur DIX paires a mille euros
chacune, pour voir ce que la paire change. Ce script donne la reponse que le
passe connait deja, avant que le direct ne mette des mois a la redonner.

L'ECHELLE EST EXACTEMENT CELLE QUI TOURNE : profondeur 51 %, huit barreaux,
raison 1,6, espacement en puissance de courbure 2, objectif 2 % nets, premier
barreau a -2 %, plancher de 12 EUR, mille euros par paire. Rien n'est balaye,
rien n'est choisi : une seule configuration, dix paires, neuf blocs de cent
jours avec l'echelle remise a neuf au debut de chaque bloc.

CE QUE LE TABLEAU N'EST PAS. Ce n'est pas un classement a suivre. Trier les
paires sur ce qu'elles ont rendu et ne garder que les meilleures est exactement
l'erreur que la validation en avant a chiffree a vingt ou trente points : le
panier du paper trading est choisi sur la LIQUIDITE, mesuree sur une fenetre
commune de trente jours, et il le reste quoi que dise cette page.

Ce que le tableau sert a voir, c'est la DISPERSION : de combien deux paires
peuvent differer sous une echelle identique, et si l'ordre entre elles tient
d'un bloc au suivant. S'il ne tient pas, alors le direct ne departagera rien
avant tres longtemps, et il vaut mieux le savoir maintenant.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000

#  Recopie du profil "8paliers_concentre" de scripts/paper_grille.py. Une copie
#  et non un import : le module de paper trading ouvre la base et le reseau au
#  chargement, et cette etude doit rester lisible seule.
REGLAGE = dict(profondeur=0.51, paliers=8, ratio=1.6, objectif_net=0.02,
               depart_sous=0.02, espacement="puissance", courbure=2.0,
               suivre_hausse=True, vente_meme_bougie=False, mise_min=12.0,
               abandon_sous=0.15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--frais", type=float, default=0.001)
    ap.add_argument("--paires", default=("BTC/EUR,ETH/EUR,XRP/EUR,SOL/EUR,DOGE/EUR,"
                                         "TRX/EUR,UNI/EUR,LINK/EUR,ADA/EUR,LTC/EUR"))
    ap.add_argument("--sortie", default="docs/etude-dix-paires.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    n, t0, bloc_ms = d["n_blocs"], d["t0"], d["bloc_jours"] * MS_JOUR
    rg = Reglages(frais=args.frais, **REGLAGE)
    ech = rg.echelle(1.0, args.budget)
    print(f"Echelle : {len(ech)} barreaux a "
          + ", ".join(f"{(p-1)*100:.0f} %" for p, _ in ech))
    print("Mises   : " + ", ".join(f"{e:.0f}" for _, e in ech) + " EUR")
    print(f"{n} blocs de {d['bloc_jours']} jours, {args.budget:.0f} EUR par paire, "
          f"echelle remise a neuf a chaque bloc.\n", flush=True)

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    res, absentes = {}, []
    for s in args.paires.split(","):
        blocs = []
        for k in range(n):
            a, b = t0 + k * bloc_ms, t0 + (k + 1) * bloc_ms
            serie = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                     for x in cx.execute(
                         "SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                         "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (s, a, b))]
            #  un bloc trop troue ne se compare a rien : on l'ecarte plutot que
            #  de le compter comme une performance nulle
            if len(serie) < 0.8 * bloc_ms // 300_000:
                blocs.append(None)
                continue
            u = resume(rejouer(s, serie, rg, args.budget))
            hold = serie[-1][4] / serie[0][1] * (1 - args.frais) ** 2 - 1
            blocs.append(dict(perf=u["perf_pct"], hold=hold, cycles=u["cycles"],
                              duree=u["duree_moyenne_h"], creux=u["drawdown_max"],
                              engage=u["engage_moyen"]))
        if all(x is None for x in blocs):
            absentes.append(s)
            continue
        res[s] = blocs
    cx.close()
    if absentes:
        print("Sans historique sur la fenetre, ecartees du tableau : "
              + ", ".join(absentes) + "\n")

    vus = [k for k in range(n) if any(res[s][k] for s in res)]
    ent = "".join(f"{('b'+str(k)):>7}" for k in vus)
    print("=" * 118)
    print("PERFORMANCE PAR BLOC DE 100 JOURS, MEME ECHELLE, MILLE EUROS")
    print("=" * 118)
    print(f"{'paire':<9}{ent}{'moyenne':>10}{'pire':>8}{'>0':>6}{'creux':>8}"
          f"{'cycles':>8}{'duree':>8}{'capital':>9}")
    lignes = []
    for s in sorted(res, key=lambda s: -stt.mean(
            x["perf"] for x in res[s] if x)):
        v = [res[s][k] for k in vus]
        p = [x["perf"] for x in v if x]
        cells = "".join(("      —" if x is None else f"{x['perf']:>+7.1%}") for x in v)
        lignes.append((s, stt.mean(p), min(p)))
        print(f"{s:<9}{cells}{stt.mean(p):>+10.2%}{min(p):>+8.1%}"
              f"{sum(1 for x in p if x > 0):>3}/{len(p):<2}"
              f"{min(x['creux'] for x in v if x):>+8.1%}"
              f"{sum(x['cycles'] for x in v if x):>8}"
              f"{stt.mean(x['duree'] for x in v if x and x['cycles']):>7.0f}h"
              f"{stt.mean(x['engage'] for x in v if x):>9.1%}")
    hold = {s: [res[s][k]["hold"] for k in vus if res[s][k]] for s in res}
    print()
    print(f"{'ne rien faire':<9}" + "".join(
        ("      —" if res[list(res)[0]][k] is None else "       ") for k in vus)
        + f"{'':>10}")
    for s in sorted(hold, key=lambda s: -stt.mean(hold[s])):
        print(f"  {s:<9} ne rien faire : {stt.mean(hold[s]):>+7.2%} par bloc, "
              f"pire {min(hold[s]):>+7.1%}")

    #  --- la dispersion, qui est la vraie question posee ---
    moy = [m for _, m, _ in lignes]
    print()
    print("=" * 118)
    print("CE QUE LA PAIRE CHANGE")
    print("=" * 118)
    print(f"  meilleure paire  {lignes[0][0]:<9} {lignes[0][1]:+.2%} par bloc")
    print(f"  pire paire       {lignes[-1][0]:<9} {lignes[-1][1]:+.2%} par bloc")
    print(f"  ecart            {lignes[0][1] - lignes[-1][1]:.2%} par bloc, "
          f"soit {(lignes[0][1] - lignes[-1][1]) * 3.65:.1%} par an")
    print(f"  ecart-type entre paires : {stt.pstdev(moy):.2%} par bloc")
    print(f"  paires positives en moyenne : {sum(1 for m in moy if m > 0)}/{len(moy)}")

    #  --- l'ordre entre paires tient-il d'une moitie a l'autre ? ---
    a, b = vus[:len(vus) // 2], vus[len(vus) // 2:]
    #  Une paire absente d'une moitie ne peut pas y avoir de rang : la comparer
    #  quand meme inventerait un deplacement. XRP, qui n'a que quatre cents jours
    #  d'historique chez OKX, est dans ce cas.
    def moyenne_sur(s, blocs):
        v = [res[s][k]["perf"] for k in blocs if res[s][k]]
        return stt.mean(v) if v else None
    comparables = [s for s in res
                   if moyenne_sur(s, a) is not None and moyenne_sur(s, b) is not None]
    horsjeu = [s for s in res if s not in comparables]
    ra = sorted(comparables, key=lambda s: -moyenne_sur(s, a))
    rb = sorted(comparables, key=lambda s: -moyenne_sur(s, b))
    print()
    if horsjeu:
        print(f"  (absente d'une des deux moities, hors de ce test : {', '.join(horsjeu)})")
    print(f"{'paire':<9}{'rang sur la 1re moitie':>26}{'rang sur la 2de':>18}{'ecart':>8}")
    dep = []
    for s in ra:
        i, j = ra.index(s) + 1, rb.index(s) + 1
        dep.append(abs(i - j))
        print(f"{s:<9}{i:>26}{j:>18}{i - j:>+8}")
    print(f"\n  Deplacement moyen dans le classement : {stt.mean(dep):.1f} places sur "
          f"{len(ra)}.")
    hasard = (len(ra) ** 2 - 1) / (3 * len(ra))
    print(f"  Ce qu'un tirage au hasard donnerait : {hasard:.1f} places.")
    if stt.mean(dep) >= 0.8 * hasard:
        print("  Autant dire que l'ordre entre paires ne tient pas : le classement de la")
        print("  premiere moitie n'annonce pas celui de la seconde. Choisir ses paires sur")
        print("  leurs resultats passes n'a donc aucune valeur predictive ici.")
    else:
        print("  L'ordre tient en partie : un classement passe garde une valeur, mais il")
        print("  faut le mesurer EN AVANT avant d'en faire une regle.")

    Path(args.sortie).write_text(json.dumps(
        {"reglage": REGLAGE, "budget": args.budget, "n_blocs": n, "t0": t0,
         "bloc_jours": d["bloc_jours"], "resultats": res},
        separators=(",", ":")), encoding="utf-8")
    print(f"\nEcrit dans {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
