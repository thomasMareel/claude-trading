"""Balaye les reglages de la grille sur l'historique fin, et ecrit le classement.

    python scripts/chercher_reglages.py --tf 5m --etape large
    python scripts/chercher_reglages.py --tf 5m --etape fin
    python scripts/chercher_reglages.py --tf 1m --etape finalistes

POURQUOI DU FIN. Le moteur ne connait d'une bougie que quatre nombres et doit
deviner l'ordre dans lequel le prix les a parcourus. Sur une bougie horaire ce
pari decide tout : il autorise a acheter au plus bas puis revendre au plus haut
de la meme heure. Mesure faite, un objectif de 0,5 % rendait +6,65 % en horaire
et -0,56 % en cinq minutes : la totalite du gain etait une invention du rejeu.
Un reglage ne vaut donc rien tant qu'il n'a pas ete rejoue au pas fin.

CE QUI EST CLASSE. Pas la performance seule : un gain paye en immobilisant tout
le capital dans un creux de quarante pour cent n'est pas un bon reglage. Le
score retient la performance, penalise le creux, et exige de battre le simple
fait de ne rien faire, mesure sur la meme fenetre.
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
from src.grille import Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

#  Trois etapes, du grossier au fin. Chacune part des enseignements de la
#  precedente ; elles sont ecrites ici pour que le chemin reste reproductible.
GRILLES = {
    # large : on ratisse, en acceptant que la plupart des combinaisons soient mauvaises
    "large": dict(
        profondeur=(0.08, 0.12, 0.20, 0.30, 0.40),
        paliers=(6, 8, 12, 16),
        ratio=(1.0, 1.15, 1.3, 1.5),
        objectif_net=(0.01, 0.015, 0.02, 0.03, 0.05),
        depart_sous=(0.0, 0.02),
        reancrage_min=(0.0, 0.02),
        abandon_sous=(0.15,),
    ),
    # fin : autour de ce que "large" designe, avec les leviers laisses de cote
    "fin": dict(
        profondeur=(0.25, 0.30, 0.35, 0.40, 0.50),
        paliers=(8, 10, 12, 16, 20),
        ratio=(1.15, 1.3, 1.4, 1.5),
        objectif_net=(0.02, 0.025, 0.03, 0.04, 0.05),
        depart_sous=(0.0, 0.01, 0.02),
        reancrage_min=(0.0, 0.01, 0.03),
        abandon_sous=(0.10, 0.15, 0.25, 0.40),
    ),
}


def bougies(st: Storage, symbole: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (symbole, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def evaluer(data: dict, params: dict, frais: float, budget: float, plancher: float) -> dict | None:
    rg = Reglages(frais=frais, mise_min=plancher, vente_meme_bougie=False,
                  suivre_hausse=True, **params)
    #  Une echelle dont le premier barreau est sous le plancher n'est pas celle
    #  qu'on croit tester : son sommet n'existe pas. On l'ecarte plutot que de
    #  la classer sur un comportement ampute.
    if rg.echelle(1.0, budget)[0][1] < plancher:
        return None
    perf, cyc, dd, eng, ab, tps = [], 0, 0.0, [], 0, []
    for s, b in data.items():
        r = rejouer(s, b, rg, budget, trace=False)
        u = resume(r)
        perf.append(u["perf_pct"])
        cyc += len(r["cycles"])
        dd = min(dd, u["drawdown_max"])
        eng.append(u["engage_moyen"])
        tps.append(u["part_temps_engage"])
        ab += r["abandons"]
    n = len(data)
    return dict(params=params, perf=sum(perf) / n, pire_paire=min(perf), cycles=cyc // n,
                drawdown=dd, engage=sum(eng) / n, temps_engage=sum(tps) / n, abandons=ab)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--etape", default="large", choices=[*GRILLES, "finalistes"])
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--entree", default=None, help="finalistes : le JSON d'une etape precedente")
    ap.add_argument("--garder", type=int, default=40)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    data = {s: bougies(st, s, args.tf) for s in cfg.symbols}
    data = {s: b for s, b in data.items() if len(b) > 1000}
    if not data:
        raise SystemExit(f"aucun historique {args.tf} ; lance scripts/fetch_fin.py --tf {args.tf}")
    n = min(len(b) for b in data.values())
    jours = (max(b[-1][0] for b in data.values()) - min(b[0][0] for b in data.values())) / 86_400_000
    hold = sum((b[-1][4] / b[0][1]) * (1 - frais) ** 2 - 1 for b in data.values()) / len(data)
    print(f"{len(data)} paires x {n} bougies {args.tf}, {jours:.0f} jours. "
          f"Ne rien faire : {hold:+.1%}", flush=True)

    if args.etape == "finalistes":
        if not args.entree:
            raise SystemExit("--entree est requis pour l'etape finalistes")
        precedent = json.loads(Path(args.entree).read_text(encoding="utf-8"))
        combos = [r["params"] for r in precedent["classement"][:args.garder]]
    else:
        g = GRILLES[args.etape]
        cles = list(g)
        combos = [dict(zip(cles, v)) for v in itertools.product(*(g[k] for k in cles))]
    print(f"{len(combos)} reglages a evaluer...", flush=True)

    res, t0, ecartes = [], time.time(), 0
    for i, params in enumerate(combos, 1):
        try:
            r = evaluer(data, params, frais, args.budget, args.plancher)
        except Exception:                              # noqa: BLE001
            r = None                                   # reglage invalide : objectif sous les frais
        if r is None:
            ecartes += 1
        else:
            res.append(r)
        if i % 50 == 0 or i == len(combos):
            e = time.time() - t0
            print(f"  {i}/{len(combos)}  ({e / 60:.1f} min, reste ~{e / i * (len(combos) - i) / 60:.1f} min)",
                  flush=True)

    #  Un gain obtenu en traversant un creux de quarante pour cent n'est pas le
    #  meme qu'un gain obtenu a plat : le creux compte pour moitie.
    for r in res:
        r["score"] = r["perf"] + r["drawdown"] * 0.5
    res.sort(key=lambda r: -r["score"])
    sortie = Path(f"docs/recherche-{args.etape}-{args.tf}.json")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(
        {"tf": args.tf, "etape": args.etape, "jours": round(jours), "hold": hold,
         "budget": args.budget, "plancher": args.plancher, "frais": frais,
         "evalues": len(res), "ecartes": ecartes, "classement": res},
        separators=(",", ":")), encoding="utf-8")

    print(f"\n{len(res)} reglages evalues, {ecartes} ecartes (echelle amputee ou objectif "
          f"sous les frais). Ecrit dans {sortie}\n")
    ent = (f"{'prof':>6}{'pal':>5}{'ratio':>7}{'obj':>7}{'depart':>8}{'reanc':>7}{'aband':>7}"
           f"{'perf':>9}{'pire paire':>12}{'creux':>8}{'cyc/sem':>9}{'engage':>8}")
    for titre, cle in (("LES 20 MEILLEURS SCORES", lambda r: -r["score"]),
                       ("LES 10 PLUS RENTABLES", lambda r: -r["perf"]),
                       ("LES 10 PLUS ACTIFS", lambda r: -r["cycles"])):
        print(f"=== {titre} ===\n{ent}")
        for r in sorted(res, key=cle)[:20 if "SCORES" in titre else 10]:
            p = r["params"]
            print(f"{p['profondeur']:>5.0%}{p['paliers']:>5}{p['ratio']:>7}{p['objectif_net']:>7.1%}"
                  f"{p['depart_sous']:>8.0%}{p['reancrage_min']:>7.0%}{p['abandon_sous']:>7.0%}"
                  f"{r['perf'] * 100:>+8.2f}%{r['pire_paire'] * 100:>+11.2f}%{r['drawdown'] * 100:>7.0f}%"
                  f"{r['cycles'] / (jours / 7):>9.2f}{r['engage'] * 100:>7.0f}%")
        print()
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
