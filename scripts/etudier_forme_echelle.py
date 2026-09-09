"""Comment couvrir le pire SANS que les barreaux du bas soient une fiction.

    python scripts/etudier_forme_echelle.py

CE QUE LA PREMIERE ETUDE A ETABLI (scripts/etudier_reancrage.py) :

  - le seuil de reancrage N'EST PAS le remede. L'augmenter fait DESCENDRE la
    part du capital au travail (7,2 % a 0 %, 3,7 % a 2 %, 1,5 % a 8 %) et TUE
    des barreaux supplementaires. La raison est mecanique : un ancrage collant
    reste plus bas que le prix pendant les hausses, donc le premier barreau est
    touche moins souvent, et les barreaux profonds — mesures depuis cet ancrage
    plus bas — demandent une chute encore plus grande. La remede propose par la
    revue etait faux, et c'est la mesure qui le dit.

  - la profondeur, elle, commande tout : a -52 % le dernier barreau n'est jamais
    achete et 7 % du capital travaille ; a -32 % aucun barreau ne meurt ; a
    -24 %, 22 % du capital travaille.

  - mais la pire chute de BTC sous son plus haut glissant, sur ces 900 jours,
    est de 52,9 %. Votre regle — "le dernier achat au plus bas enregistre, pour
    prevoir le pire" — exige donc une echelle qui descend a -53 %, et une
    echelle a barreaux equidistants qui descend a -53 % place la moitie de ses
    barreaux la ou le prix ne va jamais.

CE QUE CE SCRIPT CHERCHE : une echelle qui descende bien a -53 % tout en gardant
ses barreaux du haut dans la zone ou le prix vit reellement. Deux leviers pour
cela, aucun ne touche a la profondeur :

  espacement   "puissance" avec une courbure > 1 resserre les barreaux en haut
               et etire ceux du bas, sans changer ni le premier ni le dernier.
               "geometrique" espace en pourcentage plutot qu'en euros.
  ratio        la progression des mises. Plus elle est forte, plus l'argent
               dort en bas ; l'abaisser ramene du capital dans la zone vivante.

LA REGLE DE CHOIX, ECRITE AVANT DE VOIR LES RENDEMENTS : parmi les echelles qui
descendent au moins a la pire chute observee ET dont CHAQUE barreau est achete
au moins une fois sur la fenetre, on retient celle qui met la plus grande part
du capital au travail. Le rendement est affiche mais ne choisit rien.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000
_SER: dict = {}

#  profondeur fixee par VOTRE contrainte : 0,51 + depart 0,02 = dernier barreau
#  a -53 %, juste sous la pire chute de BTC (52,9 %).
FORMES = dict(
    espacement=("lineaire", "puissance", "geometrique"),
    courbure=(1.0, 1.5, 2.0, 2.5, 3.0),
    ratio=(1.15, 1.3, 1.45, 1.6),
)


def pire_chute(b) -> float:
    haut, pire = b[0][2], 0.0
    for x in b:
        haut = max(haut, x[2])
        pire = min(pire, x[3] / haut - 1)
    return -pire


def _init(db, paires, t0, t1):
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for s in paires:
        _SER[s] = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
                   cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                              "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (s, t0, t1))]
    cx.close()


def _tache(job):
    p, budget, plancher, fixe = job
    try:
        rg = Reglages(frais=0.001, mise_min=plancher, **fixe, **p)
    except GrilleError:
        return []
    ech = rg.echelle(1.0, budget)
    if ech[0][1] < plancher:            # premier barreau sous le plancher : echelle amputee
        return []
    mises = [m for _, m in ech]
    out = []
    for s, b in _SER.items():
        r = rejouer(s, b, rg, budget, trace=True)
        u = resume(r)
        coups = [0] * len(ech)
        for e in r["journal"]:
            if e.genre == "achat":
                coups[min(range(len(mises)), key=lambda k: abs(mises[k] - e.euros))] += 1
        out.append(dict(paire=s, **p, perf=u["perf_pct"], engage=u["engage_moyen"],
                        creux=u["drawdown_max"], cycles=u["cycles"],
                        duree=u["duree_moyenne_h"], coups=coups,
                        niveaux=[round(pr - 1, 4) for pr, _ in ech], mises=[round(m) for m in mises]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--paliers", type=int, default=8)
    ap.add_argument("--profondeur", type=float, default=0.51)
    ap.add_argument("--paire-retenue", default="BTC/EUR")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--sortie", default="docs/etude-forme-echelle.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    paires, n = d["paires"], d["n_blocs"]
    t0, t1 = d["t0"], d["t0"] + n * d["bloc_jours"] * MS_JOUR
    fixe = d["fixe"]

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    b = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
         cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                    "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                    (args.paire_retenue, t0, t1))]
    cx.close()
    exigee = pire_chute(b)
    bas = args.profondeur + 0.02
    print(f"Pire chute de {args.paire_retenue} sous son plus haut glissant : {exigee:.1%}")
    print(f"Echelle testee : {args.paliers} barreaux, dernier a -{bas:.0%}, "
          f"{args.budget:.0f} EUR, {n * d['bloc_jours']} jours en continu.")
    print(f"REGLE : dernier barreau au moins a -{exigee:.0%}, CHAQUE barreau achete au")
    print("moins une fois, puis la plus grande part de capital au travail. "
          "Le rendement ne choisit pas.\n")
    if bas < exigee:
        print(f"  ATTENTION : -{bas:.0%} ne couvre pas -{exigee:.1%}.\n")

    cles = list(FORMES)
    jobs = []
    vus = set()
    for vals in itertools.product(*(FORMES[k] for k in cles)):
        p = dict(zip(cles, vals))
        #  la courbure n'a de sens que pour l'espacement "puissance" : ailleurs
        #  elle est ignoree et produirait des doublons
        if p["espacement"] != "puissance":
            p["courbure"] = 1.0
        p.update(profondeur=args.profondeur, paliers=args.paliers, objectif_net=0.02,
                 depart_sous=0.02)
        cle = tuple(sorted(p.items()))
        if cle in vus:
            continue
        vus.add(cle)
        jobs.append((p, args.budget, args.plancher, fixe))

    lignes = []
    with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                             initargs=(args.db, paires, t0, t1)) as ex:
        for lot in ex.map(_tache, jobs):
            lignes.extend(lot)
    Path(args.sortie).write_text(json.dumps(lignes, separators=(",", ":")), encoding="utf-8")

    ret = [x for x in lignes if x["paire"] == args.paire_retenue]
    ret.sort(key=lambda x: (x["espacement"], x["courbure"], x["ratio"]))
    print("=" * 108)
    print(f"{args.paire_retenue}")
    print("=" * 108)
    print(f"{'espacement':<13}{'courbure':>9}{'ratio':>7}{'capital au travail':>20}"
          f"{'barreaux morts':>16}{'rendement':>11}{'creux':>9}{'cycles':>8}{'duree':>8}")
    for x in ret:
        morts = [i for i, c in enumerate(x["coups"]) if c == 0]
        print(f"{x['espacement']:<13}{x['courbure']:>9.1f}{x['ratio']:>7.2f}"
              f"{x['engage']:>19.1%}{(str(morts) if morts else 'aucun'):>16}"
              f"{x['perf']:>+11.1%}{x['creux']:>+9.1%}{x['cycles']:>8}{x['duree']:>7.0f}h")

    eligibles = [x for x in ret if all(c > 0 for c in x["coups"]) and bas >= exigee]
    if not eligibles:
        eligibles = [x for x in ret if all(c > 0 for c in x["coups"])]
        if eligibles:
            print("\n(la contrainte de profondeur n'est pas atteinte ; on retient malgre")
            print(" tout les echelles dont chaque barreau vit)")
    if not eligibles:
        print("\nAucune forme ne rend tous ses barreaux atteignables a cette profondeur.")
        moins = min(ret, key=lambda x: sum(1 for c in x["coups"] if c == 0))
        morts = [i for i, c in enumerate(moins["coups"]) if c == 0]
        print(f"La moins mauvaise : {moins['espacement']} courbure {moins['courbure']}, "
              f"ratio {moins['ratio']}, barreaux morts {morts}, "
              f"capital au travail {moins['engage']:.1%}.")
        return 1

    choisi = max(eligibles, key=lambda x: x["engage"])
    print("\n" + "=" * 108)
    print("FORME RETENUE PAR LA REGLE (pas par le rendement)")
    print("=" * 108)
    print(f"  espacement {choisi['espacement']}, courbure {choisi['courbure']}, "
          f"ratio {choisi['ratio']}, profondeur {args.profondeur:.0%}")
    print(f"  niveaux : " + ", ".join(f"{v:+.0%}" for v in choisi["niveaux"]))
    print(f"  mises   : " + ", ".join(f"{m}" for m in choisi["mises"]) + " EUR")
    print(f"  achats par barreau : {choisi['coups']}")
    print(f"  capital au travail : {choisi['engage']:.1%}")
    print(f"  pour information : {choisi['perf']:+.1%} sur la fenetre, creux "
          f"{choisi['creux']:+.1%}, {choisi['cycles']} cycles, duree moyenne "
          f"{choisi['duree']:.0f} h")
    print("\n  La MEME forme sur les autres paires, sans re-choisir :")
    for s in paires:
        x = next((y for y in lignes if y["paire"] == s
                  and y["espacement"] == choisi["espacement"]
                  and y["courbure"] == choisi["courbure"]
                  and y["ratio"] == choisi["ratio"]), None)
        if x:
            morts = sum(1 for c in x["coups"] if c == 0)
            print(f"    {s:<10} capital {x['engage']:>5.1%}, rendement {x['perf']:>+7.1%}, "
                  f"creux {x['creux']:>+6.1%}, barreaux morts {morts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
