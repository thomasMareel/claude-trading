"""La revente, remesuree sur 900 jours au lieu de 400. L'echelle ne bouge pas.

    python scripts/etudier_revente_900j.py

L'ETUDE DE LA REVENTE A DEJA EU LIEU, sur 400 jours, trois paires choisies a la
main et mille euros PAR paire. Elle a produit des conclusions de mecanisme qui
tiennent toujours — le cliquet ne paie pas, couper la queue au temps detruit le
gain, l'objectif est ce qui commande la duree — mais ses CHIFFRES ont ete
mesures sur la periode meme qui avait servi a les choisir. La validation en
avant a depuis chiffre ce biais a 4,82 points par bloc de cent jours. Les
niveaux publies alors ne valent donc plus rien ; les classements, peut-etre.

CE SCRIPT REFAIT LA MESURE SUR LES MEMES NEUF BLOCS DE CENT JOURS que la
validation en avant, cinq paires, echelle remise a neuf a chaque bloc. L'echelle
d'ACHAT est figee — celle du paper trading, profondeur 50 %, trois barreaux,
raison 3,3, depart -2 % — et seule la facon de REVENDRE varie :

  objectif_net       le gain vise, net de frais. C'est le bouton qui commande la
                     duree d'un cycle, la seule chose qui rapprochait des douze
                     heures visees.
  objectif_profond   relacher la cible a mesure que l'echelle se remplit : un lot
                     qui a mange tout le budget attend moins longtemps qu'un lot
                     d'un seul barreau.
  cliquet_pas        None = vente ferme a l'objectif. 0 = un stop qui suit le
                     prix en continu. 0,02 = l'escalier demande. 10 = un stop
                     FIXE qui ne monte jamais, bras de controle indispensable :
                     sans lui, tout gain serait attribue au cliquet alors qu'il
                     vient peut-etre du simple passage a un ordre au marche.
  vente_meme_bougie  autoriser l'aller-retour dans la meme bougie.

CE QU'ON EN TIRE, et qui n'a pas la meme valeur : la moyenne sur les neuf blocs
est une lecture APRES COUP, donc optimiste. La lecture honnete est la meme que
pour l'echelle : choisir sur les trois blocs precedents, relever sur le suivant.
Les deux sont imprimees, et l'ecart entre elles est le surajustement de la
revente.
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
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000
_BLOCS: dict = {}
_CFG: dict = {}

#  L'echelle d'ACHAT, figee. C'est celle qui tourne en paper trading depuis le
#  8 septembre : la question posee ne porte que sur la sortie.
ECHELLE = dict(profondeur=0.50, paliers=3, ratio=3.3, depart_sous=0.02,
               abandon_sous=0.15, suivre_hausse=True)

SORTIES = dict(
    objectif_net=(0.005, 0.008, 0.012, 0.02, 0.03, 0.04, 0.06, 0.09),
    objectif_profond=(None, 0.008, 0.015),
    cliquet_pas=(None, 0.0, 0.02, 10.0),
    vente_meme_bougie=(False, True),
)


def arms(frais: float, plancher: float, budget: float) -> list[dict]:
    cles = list(SORTIES)
    bons = []
    for vals in itertools.product(*(SORTIES[k] for k in cles)):
        p = dict(zip(cles, vals))
        if p["cliquet_pas"] is None:
            #  le retrait et les frais taker ne veulent rien dire sans cliquet :
            #  on les laisse a leur defaut pour ne pas fabriquer des doublons
            q = dict(p)
        else:
            #  retrait 1,5 % : mesure sur 400 jours, en dessous aucun cycle ne
            #  monte jamais d'un cran et le cliquet est un stop fixe deguise.
            q = dict(p, cliquet_retrait=0.015)
        try:
            Reglages(frais=frais, mise_min=plancher, **ECHELLE, **q)
        except GrilleError:
            continue
        bons.append(q)
    return bons


def _init(db: str, paires: list[str], t0: int, bloc_ms: int, n: int, cfg: dict):
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
    rg = Reglages(frais=_CFG["frais"], mise_min=_CFG["plancher"], **ECHELLE, **p)
    out = []
    for (s, k), b in _BLOCS.items():
        u = resume(rejouer(s, b, rg, _CFG["budget"], trace=False))
        out.append((i, k, s, round(u["perf_pct"], 6), u["cycles"],
                    round(u["duree_moyenne_h"], 1), round(u["drawdown_max"], 5),
                    round(u["bloque_pct"], 4)))
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
    ap.add_argument("--sortie", default="docs/etude-revente-900j.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    paires, n = d["paires"], d["n_blocs"]
    t0, bloc_ms = d["t0"], d["bloc_jours"] * MS_JOUR
    cfg = dict(frais=args.frais, plancher=args.plancher, budget=args.budget)
    A = arms(args.frais, args.plancher, args.budget)
    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    print(f"{len(paires)} paires, {n} blocs de {d['bloc_jours']} jours "
          f"({q(t0)} -> {q(t0 + n * bloc_ms)}), {args.budget:.0f} EUR par paire")
    print(f"echelle d'achat figee : profondeur {ECHELLE['profondeur']:.0%}, "
          f"{ECHELLE['paliers']} barreaux, x{ECHELLE['ratio']}, depart "
          f"-{ECHELLE['depart_sous']:.0%}")
    print(f"{len(A)} facons de revendre x {n} blocs x {len(paires)} paires = "
          f"{len(A) * n * len(paires)} rejeux\n", flush=True)

    t = time.time()
    faits = []
    with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                             initargs=(args.db, paires, t0, bloc_ms, n, cfg)) as ex:
        for j, lot in enumerate(ex.map(_tache, list(enumerate(A)), chunksize=1), 1):
            faits.extend(lot)
            if j % 20 == 0 or j == len(A):
                el = time.time() - t
                print(f"  {j}/{len(A)} — {el/60:.1f} min, reste ~{el/j*(len(A)-j)/60:.0f} min",
                      flush=True)

    Path(args.sortie).write_text(json.dumps(
        {"echelle": ECHELLE, "budget": args.budget, "paires": paires, "n_blocs": n,
         "t0": t0, "bloc_jours": d["bloc_jours"], "arms": A, "matrice": faits},
        separators=(",", ":")), encoding="utf-8")
    print(f"\nmatrice ecrite ({len(faits)} cellules, {(time.time()-t)/60:.1f} min)\n")
    lire(faits, A, paires, n, args)
    return 0


def lire(faits, A, paires, n, args):
    par = defaultdict(list)
    dur = defaultdict(list)
    cyc = defaultdict(list)
    dd = defaultdict(list)
    for i, k, s, perf, c, du, creux, blq in faits:
        par[(i, k)].append(perf)
        dur[i].append(du)
        cyc[i].append(c)
        dd[i].append(creux)
    moy = {ik: sum(v) / len(v) for ik, v in par.items()}
    tot = {i: sum(moy[(i, k)] for k in range(n) if (i, k) in moy) / n for i in range(len(A))}
    duree = {i: sum(v) / len(v) for i, v in dur.items()}
    cycles = {i: sum(v) / len(v) for i, v in cyc.items()}
    creux = {i: min(v) for i, v in dd.items()}

    def nom(p):
        c = ("vente ferme" if p["cliquet_pas"] is None else
             "stop suiveur" if p["cliquet_pas"] == 0.0 else
             "stop FIXE" if p["cliquet_pas"] >= 1 else f"cliquet {p['cliquet_pas']:.0%}")
        prof = "" if p["objectif_profond"] is None else f", fond {p['objectif_profond']:.1%}"
        mb = ", meme bougie" if p["vente_meme_bougie"] else ""
        return f"{p['objectif_net']:>5.1%} {c}{prof}{mb}"

    print("=" * 100)
    print("LES DIX MEILLEURES FACONS DE REVENDRE — moyenne sur les 9 blocs (lecture APRES COUP)")
    print("=" * 100)
    print(f"{'facon de revendre':<48}{'moy/bloc':>10}{'pire bloc':>11}{'creux':>9}"
          f"{'cycles':>8}{'duree':>8}")
    for i in sorted(tot, key=lambda i: -tot[i])[:10]:
        pire = min(moy[(i, k)] for k in range(n) if (i, k) in moy)
        print(f"{nom(A[i]):<48}{tot[i]:>+10.2%}{pire:>+11.2%}{creux[i]:>+9.1%}"
              f"{cycles[i]:>8.0f}{duree[i]:>7.0f}h")

    print()
    print("=" * 100)
    print("CE QUE CHAQUE BOUTON FAIT, TOUTES CHOSES EGALES PAR AILLEURS")
    print("=" * 100)
    for cle in ("objectif_net", "cliquet_pas", "objectif_profond", "vente_meme_bougie"):
        groupes = defaultdict(list)
        for i, p in enumerate(A):
            groupes[p[cle]].append(i)
        print(f"  {cle}")
        for v in sorted(groupes, key=lambda x: (x is None, x)):
            g = groupes[v]
            print(f"      {str(v):<8} gain {sum(tot[i] for i in g)/len(g):>+7.2%}/bloc   "
                  f"duree {sum(duree[i] for i in g)/len(g):>5.0f} h   "
                  f"cycles {sum(cycles[i] for i in g)/len(g):>5.0f}   "
                  f"pire creux {min(creux[i] for i in g):>+6.1%}")

    print()
    print("=" * 100)
    print("LA CONTRAINTE DES DOUZE HEURES : ce qui s'en approche, et ce que cela coute")
    print("=" * 100)
    print(f"{'facon de revendre':<48}{'duree':>8}{'moy/bloc':>11}{'cycles':>9}")
    for i in sorted(tot, key=lambda i: duree[i])[:8]:
        print(f"{nom(A[i]):<48}{duree[i]:>7.0f}h{tot[i]:>+11.2%}{cycles[i]:>9.0f}")

    #  --- la seule lecture honnete : choisir sur le passe, relever sur la suite ---
    T = args.apprentissage
    hon, apres = [], []
    print()
    print("=" * 100)
    print(f"CHOISI DANS LE PASSE — la facon de revendre retenue sur les {T} blocs")
    print("                       precedents, relevee sur le bloc suivant, jamais vu")
    print("=" * 100)
    for k in range(T, n):
        cand = [i for i in range(len(A)) if all((i, j) in moy for j in range(k - T, k))
                and (i, k) in moy]
        if not cand:
            continue
        best = max(cand, key=lambda i: sum(moy[(i, j)] for j in range(k - T, k)) / T
                   + 0.5 * min(moy[(i, j)] for j in range(k - T, k)))
        appris = sum(moy[(best, j)] for j in range(k - T, k)) / T
        hon.append(moy[(best, k)])
        print(f"  bloc {k}  {nom(A[best]):<46} appris {appris:+7.2%}  ->  "
              f"teste {moy[(best, k)]:+7.2%}")
    if hon:
        meilleur = max(tot, key=lambda i: tot[i])
        apres = [moy[(meilleur, k)] for k in range(T, n) if (meilleur, k) in moy]
        print(f"\n  moyenne CHOISI DANS LE PASSE : {sum(hon)/len(hon):+.2%} par bloc")
        print(f"  moyenne CHOISI APRES COUP    : {sum(apres)/len(apres):+.2%} par bloc")
        print(f"  surajustement de la revente  : {sum(hon)/len(hon) - sum(apres)/len(apres):+.2%}")
        print(f"  blocs de test positifs       : {sum(1 for x in hon if x > 0)}/{len(hon)}")


if __name__ == "__main__":
    raise SystemExit(main())
