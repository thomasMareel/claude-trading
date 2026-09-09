"""Le cliquet tel que vous le decrivez, balaye sur ses vraies valeurs.

    python scripts/etudier_cliquet_900j.py

LE MECANISME, dans vos mots : « quand on est a notre objectif de benef (2 %), le
stop se place en dessous (1,5 %). Ensuite si le cours monte (3 %), le stop
precedent est annule et un nouveau est cree plus haut (2,5 %). Ainsi de suite. »

C'est exactement cliquet_pas=0 — le stop suit le prix en continu — avec
cliquet_retrait = 0,5 point. Or tout ce qui a ete mesure jusqu'ici tournait avec
un retrait de 1,5 point, valeur imposee par une mesure faite sur le cliquet
ESCALIER (cliquet_pas > 0), ou un retrait trop court empeche de monter d'un cran.
Cette contrainte ne s'applique PAS au cliquet continu, qui se redeplace a chaque
nouveau sommet. Vos valeurs n'avaient donc jamais ete essayees.

CE QUI EST BALAYE : l'objectif d'armement, le retrait, et le pas (continu contre
escalier). Trois bras de controle indispensables sans lesquels tout chiffre
serait mal attribue :

  vente ferme    l'ordre limite dormant d'aujourd'hui. Le cliquet doit le battre.
  stop FIXE      pose au seuil et jamais remonte. La difference avec le cliquet
                 est la contribution de la REMONTEE, et rien d'autre ; la
                 difference avec la vente ferme est le prix, certain, de passer
                 d'un ordre limite a un ordre au marche.
  objectif eleve une vente ferme a objectif elargi. Si elle fait aussi bien, le
                 cliquet ne sert qu'a obtenir par du code ce qu'un reglage donne.

Un cliquet vend AU MARCHE : tarif taker 0,15 % au lieu du maker 0,10 %, plus un
glissement de 0,05 %. Ce cout est dans tous les chiffres ci-dessous.

Protocole identique a la validation en avant : 9 blocs de 100 jours, 5 paires,
echelle d'achat figee (celle du paper trading). La moyenne sur les neuf blocs
est une lecture APRES COUP ; la lecture honnete, choisir sur les trois blocs
precedents et relever sur le suivant, est imprimee en dessous.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000
_BLOCS: dict = {}
_CFG: dict = {}

ECHELLE = dict(profondeur=0.50, paliers=3, ratio=3.3, depart_sous=0.02,
               abandon_sous=0.15, suivre_hausse=True, vente_meme_bougie=False)

OBJECTIFS = (0.01, 0.02, 0.03, 0.04, 0.06)
RETRAITS = (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03)
PAS = (0.0, 0.005, 0.01, 0.02)          # 0 = continu, le reste = escalier


def bras() -> list[dict]:
    out = []
    for g in OBJECTIFS:
        out.append(dict(objectif_net=g, cliquet_pas=None, _nom=f"vente ferme {g:.0%}"))
        #  bras de controle : un stop pose au seuil et jamais remonte. Le pas
        #  enorme garantit qu'aucun cran ne sera jamais franchi.
        for r in (0.005, 0.015):
            out.append(dict(objectif_net=g, cliquet_pas=10.0, cliquet_retrait=r,
                            _nom=f"stop FIXE {g:.0%}, retrait {r:.2%}"))
        for p, r in itertools.product(PAS, RETRAITS):
            nom = "continu" if p == 0 else f"escalier {p:.1%}"
            out.append(dict(objectif_net=g, cliquet_pas=p, cliquet_retrait=r,
                            _nom=f"cliquet {nom} {g:.0%}, retrait {r:.2%}"))
    bons = []
    for p in out:
        q = {k: v for k, v in p.items() if not k.startswith("_")}
        try:
            Reglages(frais=0.001, mise_min=12.0, **ECHELLE, **q)
        except GrilleError:
            continue                     # plancher non rentable : le moteur refuse
        bons.append(p)
    return bons


def _init(db, paires, t0, bloc_ms, n, cfg):
    global _CFG
    _CFG = cfg
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    attendu = bloc_ms // 300_000
    for s in paires:
        serie = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
                 cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                            "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                            (s, t0, t0 + n * bloc_ms))]
        for k in range(n):
            d, f = t0 + k * bloc_ms, t0 + (k + 1) * bloc_ms
            tr = [x for x in serie if d <= x[0] < f]
            if len(tr) >= 0.8 * attendu:
                _BLOCS[(s, k)] = tr
    cx.close()


def _tache(job):
    i, p = job
    q = {k: v for k, v in p.items() if not k.startswith("_")}
    rg = Reglages(frais=_CFG["frais"], mise_min=_CFG["plancher"], **ECHELLE, **q)
    out = []
    for (s, k), b in _BLOCS.items():
        r = rejouer(s, b, rg, _CFG["budget"], trace=True)
        u = resume(r)
        #  par quelle porte les cycles sortent-ils, et de combien de crans le
        #  cliquet est-il monte : sans cela on ne sait pas s'il a servi.
        stops = sum(1 for c in r["cycles"] if c.sortie == "stop")
        crans = sum(c.crans for c in r["cycles"])
        out.append((i, k, s, round(u["perf_pct"], 6), u["cycles"], stops, crans,
                    round(u["duree_moyenne_h"], 1), round(u["drawdown_max"], 5)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--budget", type=float, default=200.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--frais", type=float, default=0.001)
    ap.add_argument("--apprentissage", type=int, default=3)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--sortie", default="docs/etude-cliquet-900j.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    paires, n = d["paires"], d["n_blocs"]
    t0, bloc_ms = d["t0"], d["bloc_jours"] * MS_JOUR
    cfg = dict(frais=args.frais, plancher=args.plancher, budget=args.budget)
    A = bras()
    print(f"{len(A)} bras x {n} blocs x {len(paires)} paires = {len(A)*n*len(paires)} rejeux")
    print(f"echelle figee : profondeur {ECHELLE['profondeur']:.0%}, {ECHELLE['paliers']} "
          f"barreaux, x{ECHELLE['ratio']} — seule la SORTIE varie\n", flush=True)

    t = time.time()
    faits = []
    with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                             initargs=(args.db, paires, t0, bloc_ms, n, cfg)) as ex:
        for j, lot in enumerate(ex.map(_tache, list(enumerate(A)), chunksize=1), 1):
            faits.extend(lot)
            if j % 25 == 0 or j == len(A):
                el = time.time() - t
                print(f"  {j}/{len(A)} — {el/60:.1f} min, reste ~{el/j*(len(A)-j)/60:.0f} min",
                      flush=True)
    Path(args.sortie).write_text(json.dumps(
        {"echelle": ECHELLE, "budget": args.budget, "paires": paires, "n_blocs": n,
         "t0": t0, "bloc_jours": d["bloc_jours"], "bras": A, "matrice": faits},
        separators=(",", ":")), encoding="utf-8")
    print(f"\n{(time.time()-t)/60:.1f} min, matrice ecrite\n")
    lire(faits, A, n, args)
    return 0


def lire(faits, A, n, args):
    par, dur, cyc, st, cr, dd = (defaultdict(list) for _ in range(6))
    for i, k, s, perf, c, stops, crans, du, creux in faits:
        par[(i, k)].append(perf)
        dur[i].append(du)
        cyc[i].append(c)
        st[i].append(stops)
        cr[i].append(crans)
        dd[i].append(creux)
    moy = {ik: sum(v) / len(v) for ik, v in par.items()}
    tot = {i: sum(moy[(i, k)] for k in range(n) if (i, k) in moy) / n for i in range(len(A))}
    pire = {i: min(moy[(i, k)] for k in range(n) if (i, k) in moy) for i in range(len(A))}
    duree = {i: sum(v) / len(v) for i, v in dur.items()}
    cycles = {i: sum(v) for i, v in cyc.items()}
    stops = {i: sum(v) for i, v in st.items()}
    crans = {i: sum(v) for i, v in cr.items()}
    creux = {i: min(v) for i, v in dd.items()}

    def ligne(i):
        part = stops[i] / cycles[i] if cycles[i] else 0.0
        #  Le compteur de crans ne bouge QUE pour un cliquet escalier : a pas nul,
        #  armer_ou_monter reprend self.crans tel quel (grille.py, branche
        #  « pas nul : le stop suit le prix en continu »). Afficher 0,0 pour un
        #  cliquet continu ferait croire qu'il n'a jamais monte, alors qu'il monte
        #  a chaque nouveau sommet. On met un tiret plutot qu'un chiffre faux.
        p = A[i]
        cpc = ("—" if p["cliquet_pas"] in (None, 0.0)
               else f"{crans[i] / max(1, stops[i]):.1f}")
        return (f"{p['_nom']:<42}{tot[i]:>+10.2%}{pire[i]:>+11.2%}{creux[i]:>+9.1%}"
                f"{part:>13.0%}{cpc:>9}{duree[i]:>8.0f}h")

    ent = (f"{'bras':<42}{'moy/bloc':>10}{'pire bloc':>11}{'creux':>9}"
           f"{'sorties stop':>13}{'crans':>9}{'duree':>9}")

    print("=" * 100)
    print("VOS VALEURS EXACTES : objectif 2 %, stop 0,5 point en dessous, continu")
    print("=" * 100)
    print(ent)
    vise = [i for i, p in enumerate(A) if p["objectif_net"] == 0.02
            and (p["cliquet_pas"] in (None, 0.0, 10.0))
            and p.get("cliquet_retrait", 0.005) == 0.005]
    for i in sorted(vise, key=lambda i: -tot[i]):
        print(ligne(i))

    print()
    print("=" * 100)
    print("LE RETRAIT : de combien le stop doit-il rester sous le sommet ? (objectif 2 %, continu)")
    print("=" * 100)
    print(ent)
    g = [i for i, p in enumerate(A) if p["objectif_net"] == 0.02 and p["cliquet_pas"] == 0.0]
    for i in sorted(g, key=lambda i: A[i]["cliquet_retrait"]):
        print(ligne(i))
    ref = next((i for i, p in enumerate(A)
                if p["objectif_net"] == 0.02 and p["cliquet_pas"] is None), None)
    if ref is not None:
        print(ligne(ref) + "   <- le concurrent a battre")

    print()
    print("=" * 100)
    print("LES DIX MEILLEURS BRAS, TOUS OBJECTIFS CONFONDUS")
    print("=" * 100)
    print(ent)
    for i in sorted(tot, key=lambda i: -tot[i])[:10]:
        print(ligne(i))

    print()
    print("=" * 100)
    print("A QUI REVIENT LE MERITE ? moyenne par famille, tous objectifs et retraits")
    print("=" * 100)
    fam = defaultdict(list)
    for i, p in enumerate(A):
        f = ("vente ferme" if p["cliquet_pas"] is None else
             "stop FIXE (bras de controle)" if p["cliquet_pas"] >= 1 else
             "cliquet continu" if p["cliquet_pas"] == 0 else
             f"cliquet escalier {p['cliquet_pas']:.1%}")
        fam[f].append(i)
    for f in sorted(fam, key=lambda f: -sum(tot[i] for i in fam[f]) / len(fam[f])):
        g = fam[f]
        print(f"  {f:<32}{sum(tot[i] for i in g)/len(g):>+9.2%}/bloc"
              f"{sum(duree[i] for i in g)/len(g):>9.0f} h"
              f"{sum(stops[i] for i in g)/max(1,sum(cycles[i] for i in g)):>12.0%} par stop")

    T = args.apprentissage
    hon = []
    print()
    print("=" * 100)
    print(f"CHOISI DANS LE PASSE — retenu sur les {T} blocs precedents, releve sur le suivant")
    print("=" * 100)
    for k in range(T, n):
        cand = [i for i in range(len(A)) if all((i, j) in moy for j in range(k - T, k))
                and (i, k) in moy]
        if not cand:
            continue
        best = max(cand, key=lambda i: sum(moy[(i, j)] for j in range(k - T, k)) / T
                   + 0.5 * min(moy[(i, j)] for j in range(k - T, k)))
        hon.append(moy[(best, k)])
        print(f"  bloc {k}  {A[best]['_nom']:<44} teste {moy[(best, k)]:+7.2%}")
    if hon:
        meilleur = max(tot, key=lambda i: tot[i])
        apres = [moy[(meilleur, k)] for k in range(T, n) if (meilleur, k) in moy]
        print(f"\n  CHOISI DANS LE PASSE : {sum(hon)/len(hon):+.2%} par bloc")
        print(f"  CHOISI APRES COUP    : {sum(apres)/len(apres):+.2%} par bloc")
        print(f"  surajustement        : {sum(hon)/len(hon) - sum(apres)/len(apres):+.2%}")


if __name__ == "__main__":
    raise SystemExit(main())
