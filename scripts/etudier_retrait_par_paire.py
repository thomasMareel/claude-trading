"""Le meilleur retrait, crypto par crypto — et s'il tient d'une periode a l'autre.

    python scripts/etudier_retrait_par_paire.py

LA DEMANDE : « fais plusieurs essais, sur chaque crypto, pour trouver le
meilleur retrait de point. » C'est fait ici, sur onze paires qui couvrent les
neuf cents jours, un retrait balaye de 0,10 a 3,00 point, cliquet CONTINU —
celui qui se redeplace a chaque nouveau sommet.

MAIS TROUVER LE MEILLEUR RETRAIT D'UNE PAIRE NE SUFFIT PAS, et c'est tout
l'objet du second tableau. Sur neuf blocs, avec treize retraits et onze paires,
il y a toujours un gagnant : le hasard en fabrique un. La seule question qui
compte est de savoir si le gagnant du passe reste le gagnant ensuite. Trois
epreuves y repondent, dans cet ordre :

  1. LE MEILLEUR PAR PAIRE, sur les neuf blocs. Lecture apres coup, donc
     optimiste : c'est le chiffre qu'on publierait si l'on ne se mefiait pas.

  2. LA MOITIE CONTRE L'AUTRE. Le meilleur retrait des blocs 0 a 4 est-il encore
     le meilleur des blocs 5 a 8 ? Si les deux moities ne s'accordent pas, le
     reglage par paire est du bruit, et le tableau 1 est un mirage.

  3. REGLER PAR PAIRE CONTRE UN REGLAGE COMMUN, en validation en avant. A chaque
     bloc on choisit sur les trois precedents — une fois un retrait par paire,
     une fois un seul retrait pour tout le panier — et on releve sur le bloc
     suivant, jamais vu. Si le reglage par paire ne bat pas le reglage commun
     la, il ne sert a rien : il coute des degres de liberte sans rien rendre.

L'echelle d'achat ne bouge pas : profondeur 50 %, trois barreaux, raison 3,3,
depart -2 %. Seule la sortie varie. Un stop sort AU MARCHE, taker 0,15 % plus
0,05 % de glissement, cout inclus partout.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000
_BLOCS: dict = {}
_CFG: dict = {}

ECHELLE = dict(profondeur=0.50, paliers=3, ratio=3.3, depart_sous=0.02,
               abandon_sous=0.15, suivre_hausse=True, vente_meme_bougie=False)

OBJECTIFS = (0.02, 0.03, 0.04)
RETRAITS = (0.001, 0.002, 0.003, 0.004, 0.005, 0.0065, 0.008, 0.01,
            0.0125, 0.015, 0.02, 0.025, 0.03)


def bras() -> list[dict]:
    out = []
    for g in OBJECTIFS:
        out.append(dict(objectif_net=g, cliquet_pas=None, cliquet_retrait=0.0,
                        _nom=f"vente ferme {g:.0%}"))
        for r in RETRAITS:
            out.append(dict(objectif_net=g, cliquet_pas=0.0, cliquet_retrait=r,
                            _nom=f"cliquet {g:.0%} retrait {r:.2%}"))
    bons = []
    for p in out:
        q = {k: v for k, v in p.items() if not k.startswith("_")}
        try:
            Reglages(frais=0.001, mise_min=12.0, **ECHELLE, **q)
        except GrilleError:
            continue          # le moteur refuse un plancher non rentable
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
        u = resume(rejouer(s, b, rg, _CFG["budget"], trace=False))
        out.append((i, k, s, round(u["perf_pct"], 6), u["cycles"],
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
    ap.add_argument("--sortie", default="docs/etude-retrait-par-paire.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    n, t0, bloc_ms = d["n_blocs"], d["t0"], d["bloc_jours"] * MS_JOUR
    t1 = t0 + n * bloc_ms
    #  Toutes les paires qui couvrent la fenetre, pas seulement les cinq de la
    #  validation : la question porte sur CHAQUE crypto.
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    attendu = (t1 - t0) // 300_000
    paires = [s for (s,) in cx.execute(
        "SELECT DISTINCT symbol FROM candles WHERE timeframe='5m' ORDER BY symbol")
        if cx.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe='5m' "
                      "AND ts>=? AND ts<?", (s, t0, t1)).fetchone()[0] >= 0.98 * attendu]
    cx.close()
    A = bras()
    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    print(f"{len(paires)} paires : {', '.join(paires)}")
    print(f"{n} blocs de {d['bloc_jours']} jours, {q(t0)} -> {q(t1)}, "
          f"{args.budget:.0f} EUR par paire")
    print(f"{len(A)} bras x {n} blocs x {len(paires)} paires = "
          f"{len(A)*n*len(paires)} rejeux\n", flush=True)

    cfg = dict(frais=args.frais, plancher=args.plancher, budget=args.budget)
    t = time.time()
    faits = []
    with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                             initargs=(args.db, paires, t0, bloc_ms, n, cfg)) as ex:
        for j, lot in enumerate(ex.map(_tache, list(enumerate(A)), chunksize=1), 1):
            faits.extend(lot)
            if j % 10 == 0 or j == len(A):
                el = time.time() - t
                print(f"  {j}/{len(A)} — {el/60:.1f} min, reste ~{el/j*(len(A)-j)/60:.0f} min",
                      flush=True)
    Path(args.sortie).write_text(json.dumps(
        {"echelle": ECHELLE, "budget": args.budget, "paires": paires, "n_blocs": n,
         "t0": t0, "bloc_jours": d["bloc_jours"], "bras": A, "matrice": faits},
        separators=(",", ":")), encoding="utf-8")
    print(f"\n{(time.time()-t)/60:.1f} min, matrice ecrite dans {args.sortie}\n")
    lire(faits, A, paires, n, args)
    return 0


def lire(faits, A, paires, n, args):
    #  perf[(bras, bloc, paire)] : rien n'est moyenne avant d'en avoir besoin,
    #  puisque la question porte justement sur les differences entre paires.
    perf, dur = {}, {}
    for i, k, s, p, c, du, creux in faits:
        perf[(i, k, s)] = p
        dur[(i, k, s)] = du
    idx_cliquet = [i for i, b in enumerate(A) if b["cliquet_pas"] == 0.0]
    ferme = {b["objectif_net"]: i for i, b in enumerate(A) if b["cliquet_pas"] is None}

    def sur(i, s, blocs):
        v = [perf[(i, k, s)] for k in blocs if (i, k, s) in perf]
        return sum(v) / len(v) if v else None

    tous = list(range(n))
    moitie_a, moitie_b = list(range(n // 2)), list(range(n // 2, n))

    print("=" * 108)
    print("1. LE MEILLEUR RETRAIT DE CHAQUE CRYPTO — lecture APRES COUP, sur les neuf blocs")
    print("=" * 108)
    print(f"{'paire':<10}{'objectif':>9}{'retrait':>9}{'moy/bloc':>10}{'pire bloc':>11}"
          f"{'duree':>8}   {'vente ferme, meme objectif':<26}{'ecart':>8}")
    best_global = {}
    for s in paires:
        cand = [(i, sur(i, s, tous)) for i in idx_cliquet]
        cand = [(i, v) for i, v in cand if v is not None]
        if not cand:
            continue
        i, v = max(cand, key=lambda x: x[1])
        best_global[s] = i
        b = A[i]
        pire = min(perf[(i, k, s)] for k in tous if (i, k, s) in perf)
        du = sum(dur[(i, k, s)] for k in tous if (i, k, s) in dur) / n
        j = ferme[b["objectif_net"]]
        vf = sur(j, s, tous)
        print(f"{s:<10}{b['objectif_net']:>9.0%}{b['cliquet_retrait']:>9.2%}{v:>+10.2%}"
              f"{pire:>+11.2%}{du:>7.0f}h   {vf:>+10.2%}{'':<16}{v - vf:>+8.2%}")

    print()
    print("=" * 108)
    print("2. LA MOITIE CONTRE L'AUTRE — le gagnant des blocs 0-3 gagne-t-il encore sur 4-8 ?")
    print("=" * 108)
    print(f"{'paire':<10}{'meilleur sur 0-3':>18}{'meilleur sur 4-8':>18}"
          f"{'ce que le 1er rend sur 4-8':>28}{'ce que le 2e y rend':>21}")
    tenus, ecarts = 0, []
    for s in paires:
        ca = [(i, sur(i, s, moitie_a)) for i in idx_cliquet]
        cb = [(i, sur(i, s, moitie_b)) for i in idx_cliquet]
        ca = [(i, v) for i, v in ca if v is not None]
        cb = [(i, v) for i, v in cb if v is not None]
        if not ca or not cb:
            continue
        ia, _ = max(ca, key=lambda x: x[1])
        ib, vb = max(cb, key=lambda x: x[1])
        va_sur_b = sur(ia, s, moitie_b)
        ra, rb = A[ia]["cliquet_retrait"], A[ib]["cliquet_retrait"]
        if abs(ra - rb) < 1e-9:
            tenus += 1
        ecarts.append(vb - va_sur_b)
        marque = "  <- tient" if abs(ra - rb) < 1e-9 else ""
        print(f"{s:<10}{ra:>17.2%}{rb:>18.2%}{va_sur_b:>+28.2%}{vb:>+21.2%}{marque}")
    print(f"\n  Le meilleur retrait de la premiere moitie reste le meilleur de la seconde")
    print(f"  pour {tenus} paire(s) sur {len(paires)}. Manque a gagner moyen de l'avoir")
    print(f"  choisi sur le passe : {sum(ecarts)/len(ecarts):+.2%} par bloc.")

    #  --- 3. l'epreuve qui tranche ---
    T = args.apprentissage
    print()
    print("=" * 108)
    print(f"3. REGLER PAR PAIRE CONTRE UN REGLAGE COMMUN — choix sur les {T} blocs")
    print("   precedents, releve sur le bloc suivant, jamais vu")
    print("=" * 108)
    par_paire, commun, ferme_ref = [], [], []
    choisis = []
    for k in range(T, n):
        passe = list(range(k - T, k))
        #  a) un retrait par paire
        tot = []
        for s in paires:
            cand = [(i, sur(i, s, passe)) for i in idx_cliquet]
            cand = [(i, v) for i, v in cand if v is not None and (i, k, s) in perf]
            if not cand:
                continue
            i, _ = max(cand, key=lambda x: x[1])
            tot.append(perf[(i, k, s)])
            choisis.append(A[i]["cliquet_retrait"])
        if tot:
            par_paire.append(sum(tot) / len(tot))
        #  b) un seul retrait pour tout le panier
        cand = []
        for i in idx_cliquet:
            v = [sur(i, s, passe) for s in paires]
            v = [x for x in v if x is not None]
            if v:
                cand.append((i, sum(v) / len(v)))
        if cand:
            i, _ = max(cand, key=lambda x: x[1])
            v = [perf[(i, k, s)] for s in paires if (i, k, s) in perf]
            if v:
                commun.append(sum(v) / len(v))
        #  c) le concurrent immobile : la vente ferme a 4 %
        j = ferme.get(0.04)
        if j is not None:
            v = [perf[(j, k, s)] for s in paires if (j, k, s) in perf]
            if v:
                ferme_ref.append(sum(v) / len(v))
        print(f"  bloc {k}   par paire {par_paire[-1]:+7.2%}   commun {commun[-1]:+7.2%}"
              f"   vente ferme 4 % {ferme_ref[-1]:+7.2%}")
    print()
    print(f"  UN RETRAIT PAR PAIRE, choisi sur le passe : {sum(par_paire)/len(par_paire):+.2%} par bloc")
    print(f"  UN RETRAIT COMMUN, choisi sur le passe    : {sum(commun)/len(commun):+.2%} par bloc")
    print(f"  VENTE FERME 4 %, sans rien choisir        : {sum(ferme_ref)/len(ferme_ref):+.2%} par bloc")
    gain = sum(par_paire)/len(par_paire) - sum(commun)/len(commun)
    print(f"\n  Ce que le reglage par paire rapporte de plus que le reglage commun :"
          f" {gain:+.2%} par bloc.")
    c = Counter(round(r, 5) for r in choisis)
    print(f"  Retraits retenus, par frequence : "
          + ", ".join(f"{r:.2%} ({m}x)" for r, m in c.most_common(5)))

    print()
    print("=" * 108)
    print("4. LA COURBE DU RETRAIT, PAIRE PAR PAIRE (objectif 2 %, moyenne par bloc)")
    print("=" * 108)
    g2 = [i for i in idx_cliquet if A[i]["objectif_net"] == 0.02]
    g2.sort(key=lambda i: A[i]["cliquet_retrait"])
    print(f"{'paire':<10}" + "".join(f"{A[i]['cliquet_retrait']:>7.2%}" for i in g2))
    for s in paires:
        v = [sur(i, s, tous) for i in g2]
        best = max((x for x in v if x is not None), default=None)
        cells = "".join(("     —" if x is None else
                         (f"{x:>+7.1%}" if x != best else f"{x:>+6.1%}*")) for x in v)
        print(f"{s:<10}{cells}")
    print("  * meilleur retrait de la ligne. Si l'etoile se promene d'une paire a")
    print("    l'autre sans logique, c'est du bruit ; si elle se groupe, c'est un effet.")


if __name__ == "__main__":
    raise SystemExit(main())
