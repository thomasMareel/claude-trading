"""Etudie les chutes des 400 derniers jours, pour placer les barreaux ou elles s'arretent.

    python scripts/etudier_chutes.py --tf 5m --rebond 0.01

UNE CHUTE, ici, est un mouvement de baisse continu : du sommet local jusqu'au
creux atteint avant que le prix ne reparte. Le sommet et le creux ne se
declarent qu'apres coup, quand le prix s'est retourne d'au moins `rebond` : sans
ce filtre, chaque bougie descendante compterait pour une chute et la
distribution ne mesurerait que le bruit d'echantillonnage.

SA PROFONDEUR est mesuree contre la MOYENNE DES 24 HEURES qui precedent son
sommet, et non contre le sommet lui-meme. C'est ce que demande la question
posee, et c'est aussi la bonne reference pour une grille : une echelle se pose
sous un prix de reference qu'on observe, pas sous une meche qu'on ne connaitra
qu'apres. Une chute "a 95 %" est donc une chute dont le creux vaut 95 % de cette
moyenne, soit cinq pour cent dessous.

CE QUE L'ETUDE SERT A DECIDER : ou poser le premier barreau, combien en poser, et
jusqu'ou descendre. Un barreau ne rapporte que s'il est touche PUIS revendu ;
le placer la ou les chutes s'arretent rarement, c'est immobiliser de l'argent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.storage import Storage  # noqa: E402

MIN_PAR_PAS = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


def bougies(st: Storage, s: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (s, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def moyennes24(b: list[tuple], par_jour: int) -> list[float]:
    """Moyenne glissante des clotures sur les 24 heures precedentes."""
    out, s = [], 0.0
    for i, ligne in enumerate(b):
        s += ligne[4]
        if i >= par_jour:
            s -= b[i - par_jour][4]
        out.append(s / min(i + 1, par_jour))
    return out


def chutes(b: list[tuple], rebond: float, m24: list[float]) -> list[dict]:
    """Les mouvements de baisse continus, sommet -> creux avant retournement.

    On suit un sommet courant. Des que le prix descend, on suit le creux. Le
    mouvement n'est CLOS que lorsque le prix est remonte de `rebond` depuis ce
    creux : c'est ce qui distingue une vraie chute d'un soubresaut, et c'est
    aussi ce qui definit une resistance a la baisse — un niveau ou le prix a
    cesse de descendre assez longtemps pour repartir.
    """
    out = []
    i_som, som = 0, b[0][2]
    i_creux, creux = 0, b[0][3]
    for i, (ts, o, h, lo, c) in enumerate(b):
        if h >= som and creux >= som * (1 - 1e-12):
            i_som, som = i, h                    # on monte encore : le sommet suit
            i_creux, creux = i, lo
            continue
        if lo < creux:
            i_creux, creux = i, lo               # nouveau plus bas du mouvement
        if h >= creux * (1 + rebond) and creux < som:
            ref = m24[i_som]
            out.append({
                "debut": i_som, "fin": i_creux, "sommet": som, "creux": creux, "ref24": ref,
                "chute_sommet": creux / som - 1,      # profondeur depuis le sommet
                "ratio24": creux / ref,              # le creux, en part de la moyenne 24 h
                "heures": (b[i_creux][0] - b[i_som][0]) / 3_600_000,
                "reprise_h": (ts - b[i_creux][0]) / 3_600_000,
            })
            i_som, som = i, h                    # nouveau cycle de recherche
            i_creux, creux = i, lo
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--rebond", type=float, default=0.01,
                    help="remontee minimale qui clot une chute")
    ap.add_argument("--sortie", default="docs/chutes.json")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    par_jour = 24 * 60 // MIN_PAR_PAS[args.tf]
    tout, par_paire = [], {}

    print(f"Une chute est close par un rebond de {args.rebond:.1%}. "
          f"Profondeur mesuree contre la moyenne des 24 h precedant son sommet.\n")
    for s in cfg.symbols:
        b = bougies(st, s, args.tf)
        if len(b) < 1000:
            continue
        m24 = moyennes24(b, par_jour)
        ch = chutes(b, args.rebond, m24)
        bas = min(x[3] for x in b)
        par_paire[s] = {"chutes": ch, "bas_400j": bas,
                        "bas_vs_moyenne": bas / (sum(m24) / len(m24))}
        tout += ch
        prof = sorted(x["chute_sommet"] for x in ch)
        print(f"{s:<9} {len(ch):>5} chutes | mediane {prof[len(prof)//2]:>7.2%} | "
              f"la pire {prof[0]:>7.2%} | plus bas des 400 j : {bas:,.2f} €".replace(",", " "))

    # ------------------------------------------------ le tableau demande
    print(f"\n=== QUELLE PART DES CHUTES DESCEND JUSQU'OU, "
          f"EN PART DE LA MOYENNE 24 H ===\n")
    seuils = [0.99, 0.98, 0.97, 0.96, 0.95, 0.93, 0.90, 0.85, 0.80, 0.75, 0.70, 0.60, 0.50]
    n = len(tout)
    print(f"{'le creux atteint':>18}{'part des chutes':>17}{'nombre':>9}"
          f"{'soit une baisse de':>21}")
    prec = 0
    for s in seuils:
        k = sum(1 for x in tout if x["ratio24"] <= s)
        print(f"{s:>17.0%}{k / n:>16.1%}{k:>9}{1 - s:>20.0%}")
        prec = k
    print(f"\n{n} chutes au total sur les 3 paires.")

    # ------------------------------------------------ ou elles s'arretent
    print("\n=== OU LES CHUTES S'ARRETENT : histogramme des creux ===\n")
    tranches = [(0.99, 1.01), (0.98, 0.99), (0.97, 0.98), (0.96, 0.97), (0.95, 0.96),
                (0.93, 0.95), (0.90, 0.93), (0.85, 0.90), (0.80, 0.85), (0.70, 0.80),
                (0.60, 0.70), (0.0, 0.60)]
    for a, bb in tranches:
        k = sum(1 for x in tout if a <= x["ratio24"] < bb)
        barre = "█" * max(0, round(60 * k / n))
        print(f"  {a:>5.0%} a {bb:<5.0%} {k:>5} {k / n:>6.1%} {barre}")

    Path(args.sortie).parent.mkdir(parents=True, exist_ok=True)
    Path(args.sortie).write_text(json.dumps({
        "tf": args.tf, "rebond": args.rebond, "total": n,
        "paires": {s: {"bas_400j": v["bas_400j"], "bas_vs_moyenne": v["bas_vs_moyenne"],
                       "chutes": [{k: round(x[k], 6) if isinstance(x[k], float) else x[k]
                                   for k in ("debut", "fin", "chute_sommet", "ratio24",
                                             "heures", "reprise_h")} for x in v["chutes"]]}
                   for s, v in par_paire.items()},
    }, separators=(",", ":")), encoding="utf-8")
    print(f"\nEcrit dans {args.sortie}")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
