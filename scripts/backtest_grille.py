"""Rejoue la strategie de descente sur l'historique reel, et compare les reglages.

    python scripts/backtest_grille.py                    # un rejeu par paire, reglages par defaut
    python scripts/backtest_grille.py --balayage         # compare des centaines de reglages
    python scripts/backtest_grille.py --paire BTC/EUR --detail

C'est ici que se decide "la meilleure version" de la strategie : pas a
l'opinion, mais en la rejouant sur quatre cents jours de prix reels.

A LIRE AVANT D'INTERPRETER LES RESULTATS. Un backtest flatte toujours. Les
biais connus de celui-ci, tous dans le sens optimiste :
  - le carnet est suppose infiniment profond : un ordre limite touche est
    toujours execute entierement, ce qui est faux sur les paires minces ;
  - aucun ordre n'est jamais refuse, alors qu'un post-only l'est parfois ;
  - la plateforme est supposee toujours disponible ;
  - les frais sont ceux de la configuration, pas ceux reellement preleves ;
  - le passe teste est UNE realisation du marche, pas la distribution des
    futurs possibles.
Un reglage qui gagne ici n'est donc pas un reglage qui gagnera. Il est
seulement un reglage qui n'a pas perdu sur ce chemin de prix la.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import load_config  # noqa: E402
from src.grille import Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()


def bougies(st: Storage, symbole: str, tf: str = "1h") -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (symbole, tf),
    ).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rows]


def ligne(t: Table, nom: str, s: dict, extra: str = "") -> None:
    t.add_row(
        nom, str(s["cycles"]), f"{s['cycles_par_mois']:.1f}", f"{s['gain_cumule']:+.2f}",
        f"{s['perf_pct']*100:+.2f} %", f"{s['drawdown_max']*100:.1f} %",
        f"{s['bloque_pct']*100:.0f} %", f"{s['duree_moyenne_h']:.0f} h",
        str(s["abandons"]), extra,
    )


def entete(titre: str) -> Table:
    t = Table(title=titre, pad_edge=False)
    for c, j in (("reglage", "left"), ("cycles", "right"), ("/mois", "right"), ("gain", "right"),
                 ("perf", "right"), ("pire creux", "right"), ("bloque", "right"),
                 ("duree moy.", "right"), ("abandons", "right"), ("", "left")):
        t.add_column(c, justify=j, no_wrap=True)
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paire", help="une seule paire (defaut : toutes celles de config.yaml)")
    ap.add_argument("--budget", type=float, default=1000.0, help="budget par descente, en euro")
    ap.add_argument("--balayage", action="store_true", help="compare de nombreux reglages")
    ap.add_argument("--detail", action="store_true", help="liste les cycles un par un")
    ap.add_argument("--tf", default="1h")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    paires = [args.paire] if args.paire else cfg.symbols
    frais = float(cfg.get("exchange.fee_rate", 0.001))

    data = {}
    for p in paires:
        b = bougies(st, p, args.tf)
        if len(b) < 100:
            console.print(f"[yellow]{p} : {len(b)} bougies {args.tf} seulement, ignoree. "
                          f"Lance scripts/fetch_history.py.[/]")
            continue
        data[p] = b
    if not data:
        console.print("[red]aucun historique exploitable.[/]")
        return 1
    n = min(len(b) for b in data.values())
    console.print(f"[dim]{len(data)} paire(s), {n} bougies {args.tf} chacune, soit "
                  f"{n/24:.0f} jours. Frais maker retenus : {frais:.3%}. Budget : {args.budget:.0f} EUR par descente.[/]")

    # ------------------------------------------------------------ un rejeu
    if not args.balayage:
        base = Reglages(profondeur=0.15, paliers=10, ratio=1.8, objectif_net=0.02,
                        frais=frais, abandon_sous=0.15)
        t = entete("Reglage de reference : profondeur 15 %, 10 paliers, progression 1,8, objectif 2 % net")
        for p, b in data.items():
            r = rejouer(p, b, base, args.budget)
            ligne(t, p, resume(r))
            if args.detail:
                console.print(f"\n[bold]{p}[/] : {len(r['cycles'])} cycles")
                for c in r["cycles"][:25]:
                    console.print(f"  {c.paliers} paliers, {c.investi:>8.2f} investi, "
                                  f"gain {c.gain:>+7.2f} ({c.gain_pct*100:+.2f} %), {c.heures:>5.0f} h")
        console.print(t)
        console.print("[dim]bloque = part du budget encore immobilisee a la fin, dans une descente non revendue.[/]")
        st.close()
        return 0

    # ------------------------------------------------------------ balayage
    PROFONDEURS = (0.08, 0.12, 0.15, 0.20, 0.30)
    PALIERS = (6, 10, 14)
    RATIOS = (1.0, 1.4, 1.8, 2.2)
    OBJECTIFS = (0.01, 0.02, 0.03, 0.05)
    combos = [c for c in itertools.product(PROFONDEURS, PALIERS, RATIOS, OBJECTIFS)
              if c[3] > 2 * frais]
    console.print(f"[dim]{len(combos)} reglages x {len(data)} paires = "
                  f"{len(combos)*len(data)} rejeux...[/]")

    res = []
    for prof, npal, ratio, obj in combos:
        rg = Reglages(profondeur=prof, paliers=npal, ratio=ratio, objectif_net=obj,
                      frais=frais, abandon_sous=0.15)
        agg = {"cycles": 0, "gain_cumule": 0.0, "drawdown_max": 0.0,
               "bloque_pct": 0.0, "abandons": 0, "duree": 0.0, "perf": 0.0}
        for p, b in data.items():
            s = resume(rejouer(p, b, rg, args.budget))
            agg["cycles"] += s["cycles"]
            agg["gain_cumule"] += s["gain_cumule"]
            agg["drawdown_max"] = min(agg["drawdown_max"], s["drawdown_max"])
            agg["bloque_pct"] += s["bloque_pct"] / len(data)
            agg["abandons"] += s["abandons"]
            agg["duree"] += s["duree_moyenne_h"] / len(data)
            agg["perf"] += s["perf_pct"] / len(data)
        res.append(((prof, npal, ratio, obj), agg))

    def score(kv):
        """On ne classe pas sur le gain seul : un gain obtenu en immobilisant
        tout le capital dans un creux de 40 % n'est pas un bon reglage."""
        _, a = kv
        return a["perf"] + a["drawdown_max"] * 0.5

    res.sort(key=score, reverse=True)
    t = entete("Les 15 meilleurs reglages, toutes paires confondues")
    for (prof, npal, ratio, obj), a in res[:15]:
        s = {"cycles": a["cycles"], "cycles_par_mois": a["cycles"] / (n / 24 / 30.4),
             "gain_cumule": a["gain_cumule"], "perf_pct": a["perf"],
             "drawdown_max": a["drawdown_max"], "bloque_pct": a["bloque_pct"],
             "duree_moyenne_h": a["duree"], "abandons": a["abandons"]}
        ligne(t, f"prof {prof:.0%} / {npal} pal / x{ratio} / obj {obj:.0%}", s)
    console.print(t)

    t2 = entete("Les 5 pires, pour voir ce qui casse")
    for (prof, npal, ratio, obj), a in res[-5:]:
        s = {"cycles": a["cycles"], "cycles_par_mois": a["cycles"] / (n / 24 / 30.4),
             "gain_cumule": a["gain_cumule"], "perf_pct": a["perf"],
             "drawdown_max": a["drawdown_max"], "bloque_pct": a["bloque_pct"],
             "duree_moyenne_h": a["duree"], "abandons": a["abandons"]}
        ligne(t2, f"prof {prof:.0%} / {npal} pal / x{ratio} / obj {obj:.0%}", s)
    console.print(t2)
    console.print("[dim]Classement sur perf + 0,5 x pire creux : un gain paye par un creux profond "
                  "n'est pas un bon reglage. Rappel des biais du backtest en tete de ce fichier.[/]")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
