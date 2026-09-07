"""Compare la vente ferme et le cliquet, en attribuant le gain a sa vraie cause.

    python scripts/comparer_cliquet.py --tf 5m
    python scripts/comparer_cliquet.py --tf 1m --bases 2

Le piege de ce chantier est l'absence de concurrent. Compare a la seule ancienne
version, tout chiffre positif serait attribue d'office au cliquet, alors que
celui-ci fait DEUX choses a la fois : il remplace un ordre limite dormant par un
ordre stop au marche, et il fait remonter ce stop. La premiere coute, la seconde
rapporte, et leur somme ne dit pas laquelle domine.

QUATRE BRAS, sur les memes bougies, le meme budget, les memes paires :
  A  vente ferme : ordre limite dormant, servi sur le haut, au tarif maker.
  B  stop FIXE pose au seuil et jamais remonte (cliquet_pas enorme). Zero cran.
  C  cliquet complet, avec pas et retrait balayes.
  D  vente ferme avec un objectif simplement plus eleve.

ATTRIBUTION :
  C - B  est la contribution du CLIQUET, et rien d'autre. C'est la seule
         grandeur qui repond a la question posee.
  B - A  est le prix certain du passage d'un ordre limite a un ordre stop :
         tarif taker au lieu de maker, glissement, et retrait consenti.
  D - A  est ce que rapporte un simple changement de reglage, sans une ligne de
         code ni un type d'ordre nouveau. Si D bat C, la reponse honnete est
         qu'il ne faut pas de cliquet mais un objectif plus large.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

#  Les cinq echelles a la baisse retenues, sur lesquelles l'utilisateur veut
#  construire les cinq nouvelles versions. La geometrie est figee ; seuls le
#  seuil et les reglages du cliquet varient.
BASES = [
    ("Tres profond, 3 paliers", dict(profondeur=0.40, paliers=3, ratio=3.9, depart_sous=0.04,
                                     reancrage_min=0.02)),
    ("Profond, 4 paliers", dict(profondeur=0.50, paliers=4, ratio=2.0, reancrage_min=0.02)),
    ("Le plus solide", dict(profondeur=0.50, paliers=6, ratio=1.7, depart_sous=0.02)),
    ("Patient", dict(profondeur=0.30, paliers=12, ratio=1.3)),
    ("Actif", dict(profondeur=0.50, paliers=10, ratio=1.4, reancrage_min=0.02)),
]

SEUILS = (0.01, 0.02, 0.03, 0.04, 0.06, 0.08)
PAS = (0.005, 0.01, 0.02, 0.04)
RETRAITS = (0.002, 0.004, 0.008, 0.015, 0.025)
#  L'objectif elargi du bras D : le concurrent le plus credible du cliquet, et
#  celui qu'on oublie le plus facilement de faire courir.
ELARGIS = (0.05, 0.06, 0.08, 0.10, 0.12)


def bougies(st: Storage, s: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (s, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def mesure(data: dict, params: dict, frais: float, budget: float, plancher: float) -> dict | None:
    try:
        rg = Reglages(frais=frais, mise_min=plancher, vente_meme_bougie=False,
                      suivre_hausse=True, **params)
    except GrilleError:
        return None
    if rg.echelle(1.0, budget)[0][1] < plancher:
        return None
    perf, cyc, dd, crans, stops = [], 0, 0.0, [], 0
    for s, b in data.items():
        r = rejouer(s, b, rg, budget, trace=False)
        u = resume(r)
        perf.append(u["perf_pct"])
        cyc += len(r["cycles"])
        dd = min(dd, u["drawdown_max"])
        crans += [c.crans for c in r["cycles"]]
        stops += sum(1 for c in r["cycles"] if c.sortie != "limite")
    n = len(data)
    return dict(perf=sum(perf) / n, pire=min(perf), cycles=cyc // n, creux=dd,
                crans_moyens=sum(crans) / len(crans) if crans else 0.0,
                part_montes=sum(1 for k in crans if k > 0) / len(crans) if crans else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--bases", type=int, default=5)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    data = {s: bougies(st, s, args.tf) for s in cfg.symbols}
    data = {s: b for s, b in data.items() if len(b) > 500}
    hold = sum((b[-1][4] / b[0][1]) * (1 - frais) ** 2 - 1 for b in data.values()) / len(data)
    print(f"{len(data)} paires x {min(len(b) for b in data.values())} bougies {args.tf}. "
          f"Ne rien faire : {hold:+.1%}\n", flush=True)

    t0 = time.time()
    tout = []
    for nom, geom in BASES[:args.bases]:
        print(f"=== {nom} — prof {geom['profondeur']:.0%}, {geom['paliers']} paliers, "
              f"x{geom['ratio']} ===", flush=True)
        print(f"{'seuil':>7}{'A ferme':>10}{'B stop fixe':>13}{'C cliquet':>12}"
              f"{'C-B cliquet':>13}{'B-A ordre':>12}{'pas':>7}{'retrait':>9}{'montes':>8}",
              flush=True)
        for g0 in SEUILS:
            base = {**geom, "objectif_net": g0}
            A = mesure(data, base, frais, args.budget, args.plancher)
            B = mesure(data, {**base, "cliquet_pas": 10.0, "cliquet_retrait": 0.002},
                       frais, args.budget, args.plancher)
            if A is None or B is None:
                continue
            best = None
            for pas, retrait in itertools.product(PAS, RETRAITS):
                if retrait >= pas:
                    continue
                C = mesure(data, {**base, "cliquet_pas": pas, "cliquet_retrait": retrait},
                           frais, args.budget, args.plancher)
                if C and (best is None or C["perf"] > best[0]["perf"]):
                    best = (C, pas, retrait)
            if best is None:
                continue
            C, pas, retrait = best
            print(f"{g0:>6.0%}{A['perf'] * 100:>+9.2f}%{B['perf'] * 100:>+12.2f}%"
                  f"{C['perf'] * 100:>+11.2f}%{(C['perf'] - B['perf']) * 100:>+12.2f}"
                  f"{(B['perf'] - A['perf']) * 100:>+12.2f}{pas:>7.1%}{retrait:>9.1%}"
                  f"{C['part_montes']:>8.0%}", flush=True)
            tout.append(dict(base=nom, geom=geom, seuil=g0, pas=pas, retrait=retrait,
                             A=A, B=B, C=C))
        # --- bras D : le concurrent sans une ligne de code
        dmax = None
        for obj in ELARGIS:
            D = mesure(data, {**geom, "objectif_net": obj}, frais, args.budget, args.plancher)
            if D and (dmax is None or D["perf"] > dmax[0]["perf"]):
                dmax = (D, obj)
        if dmax:
            D, obj = dmax
            cmax = max((x["C"]["perf"] for x in tout if x["base"] == nom), default=None)
            verdict = ("le cliquet garde l'avantage" if cmax is not None and cmax > D["perf"]
                       else "UN SIMPLE OBJECTIF PLUS LARGE FAIT MIEUX")
            print(f"  bras D — vente ferme, objectif elargi a {obj:.0%} : "
                  f"{D['perf'] * 100:+.2f}%  -> {verdict}", flush=True)
        print(flush=True)

    Path("docs/comparaison-cliquet.json").write_text(
        json.dumps({"tf": args.tf, "hold": hold, "budget": args.budget,
                    "resultats": tout}, separators=(",", ":")), encoding="utf-8")
    print(f"{time.time() - t0:.0f} s. Ecrit dans docs/comparaison-cliquet.json")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
