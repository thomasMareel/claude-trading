"""Soumet des reglages a des epreuves de robustesse, pour separer un vrai bord
d'une coincidence.

    python scripts/eprouver_reglages.py --entree docs/recherche-large-5m.json --garder 25
    python scripts/eprouver_reglages.py --tf 1m --entree docs/recherche-fin-5m.json

Un balayage classe sur UNE realisation du marche. Le meilleur reglage d'un
classement est donc, par construction, celui qui a le mieux epouse ce chemin de
prix precis. Mesure faite : durcir l'objectif au fond rend +13,09 % a 5 % et
-16,31 % a 8 %. Un optimum aussi etroit ne se generalise pas, il se souvient.

QUATRE EPREUVES, toutes sur des donnees deja vues mais decoupees autrement :
  par paire      un bord reel ne tient pas que sur une seule crypto ;
  par trimestre  ni sur une seule saison de marche ;
  voisinage      un sommet etroit dans l'espace des parametres est un accident,
                 un plateau est un mecanisme : on rejoue les huit voisins ;
  pas plus fin   ce qui disparait entre cinq minutes et la minute etait une
                 invention du rejeu, pas un gain.

Aucune de ces epreuves n'est un vrai hors-echantillon : elles decoupent le meme
passe. Elles eliminent les faux positifs, elles ne prouvent aucun futur.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

#  De combien on bouge chaque parametre pour sonder le voisinage. Multiplicatif
#  pour ce qui est une echelle, additif pour ce qui est un comptage.
VOISINAGE = {
    "profondeur": ("x", 0.8, 1.25),
    "objectif_net": ("x", 0.75, 1.33),
    "objectif_profond": ("x", 0.75, 1.33),
    "ratio": ("x", 0.92, 1.08),
    "paliers": ("+", -2, 2),
}


def bougies(st: Storage, s: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (s, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def perf_sur(data: dict, params: dict, frais: float, budget: float, plancher: float):
    """Rend (performance moyenne, pire paire, cycles, pire creux) ou None."""
    try:
        rg = Reglages(frais=frais, mise_min=plancher, vente_meme_bougie=False,
                      suivre_hausse=True, **params)
    except GrilleError:
        return None
    if rg.echelle(1.0, budget)[0][1] < plancher:
        return None
    perf, cyc, dd = [], 0, 0.0
    for b in data.values():
        if len(b) < 500:
            continue
        r = rejouer("x", b, rg, budget, trace=False)
        u = resume(r)
        perf.append(u["perf_pct"])
        cyc += len(r["cycles"])
        dd = min(dd, u["drawdown_max"])
    if not perf:
        return None
    return sum(perf) / len(perf), min(perf), cyc // len(perf), dd


def voisins(params: dict) -> list[dict]:
    out = []
    for cle, (mode, bas, haut) in VOISINAGE.items():
        if params.get(cle) is None:
            continue
        for d in (bas, haut):
            v = dict(params)
            v[cle] = round(params[cle] * d, 5) if mode == "x" else max(2, params[cle] + d)
            if v != params:
                out.append(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entree", required=True)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--tf-fin", default="1m", help="pas plus fin pour l'epreuve de finesse")
    ap.add_argument("--garder", type=int, default=25)
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--trimestres", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    entree = json.loads(Path(args.entree).read_text(encoding="utf-8"))
    candidats = [r["params"] for r in entree["classement"][:args.garder]]

    plein = {s: bougies(st, s, args.tf) for s in cfg.symbols}
    plein = {s: b for s, b in plein.items() if len(b) > 500}
    fin = {s: bougies(st, s, args.tf_fin) for s in cfg.symbols}
    fin = {s: b for s, b in fin.items() if len(b) > 500}
    n = min(len(b) for b in plein.values())
    hold = sum((b[-1][4] / b[0][1]) * (1 - frais) ** 2 - 1 for b in plein.values()) / len(plein)
    print(f"{len(candidats)} candidats. {len(plein)} paires x {n} bougies {args.tf}. "
          f"Ne rien faire : {hold:+.1%}")
    print(f"Pas plus fin {args.tf_fin} : "
          f"{'disponible pour ' + str(len(fin)) + ' paires' if fin else 'ABSENT, epreuve sautee'}\n")

    lignes = []
    for i, p in enumerate(candidats, 1):
        base = perf_sur(plein, p, frais, args.budget, args.plancher)
        if base is None:
            continue
        # --- par trimestre
        tri = []
        for k in range(args.trimestres):
            tranche = {s: b[k * len(b) // args.trimestres:(k + 1) * len(b) // args.trimestres]
                       for s, b in plein.items()}
            r = perf_sur(tranche, p, frais, args.budget, args.plancher)
            tri.append(r[0] if r else 0.0)
        # --- par paire
        parp = []
        for s, b in plein.items():
            r = perf_sur({s: b}, p, frais, args.budget, args.plancher)
            parp.append(r[0] if r else 0.0)
        # --- voisinage
        vs = [perf_sur(plein, v, frais, args.budget, args.plancher) for v in voisins(p)]
        vs = [v[0] for v in vs if v]
        # --- pas plus fin
        f = perf_sur(fin, p, frais, args.budget, args.plancher) if fin else None
        lignes.append(dict(params=p, perf=base[0], pire_paire=base[1], cycles=base[2],
                           creux=base[3], trimestres=tri, paires=parp,
                           voisins_moy=sum(vs) / len(vs) if vs else None,
                           voisins_pire=min(vs) if vs else None,
                           perf_fin=f[0] if f else None))
        print(f"  {i}/{len(candidats)}", end="\r", flush=True)

    #  Le classement final ne recompense pas la performance mais la SOLIDITE :
    #  le pire trimestre et la pire paire pesent autant que la moyenne, et un
    #  reglage que ses propres voisins contredisent est ecarte.
    for L in lignes:
        pires = min(L["trimestres"]) + min(L["paires"])
        vois = L["voisins_moy"] if L["voisins_moy"] is not None else L["perf"]
        fin_ = L["perf_fin"] if L["perf_fin"] is not None else L["perf"]
        L["solidite"] = 0.30 * L["perf"] + 0.25 * pires + 0.25 * vois + 0.20 * fin_ + 0.5 * L["creux"]
        L["trim_positifs"] = sum(1 for t in L["trimestres"] if t > 0)
    lignes.sort(key=lambda L: -L["solidite"])

    sortie = Path(f"docs/epreuve-{Path(args.entree).stem}.json")
    sortie.write_text(json.dumps({"source": args.entree, "tf": args.tf, "hold": hold,
                                  "classement": lignes}, separators=(",", ":")), encoding="utf-8")
    print(f"\nEcrit dans {sortie}\n")
    print(f"{'prof':>5}{'pal':>4}{'ratio':>6}{'obj':>6}{'fond':>6}{'esp':>5}{'reanc':>6}"
          f"{'perf':>8}{'pire tri':>10}{'tri+':>6}{'pire paire':>11}"
          f"{'voisins':>9}{'pire vois':>11}{f'{args.tf_fin}':>8}{'creux':>7}")
    for L in lignes[:20]:
        p = L["params"]
        g = lambda v, f="{:>+7.1%}": "      -" if v is None else f.format(v)  # noqa: E731
        print(f"{p['profondeur']:>5.0%}{p['paliers']:>4}{p['ratio']:>6}{p['objectif_net']:>6.1%}"
              f"{('%.0f%%' % (p.get('objectif_profond', 0) * 100)) if p.get('objectif_profond') else '   -':>6}"
              f"{('geo' if p.get('espacement') == 'geometrique' else 'lin'):>5}"
              f"{p.get('reancrage_min', 0):>6.0%}"
              f"{L['perf']:>+8.1%}{min(L['trimestres']):>+10.1%}{L['trim_positifs']:>4}/4"
              f"{min(L['paires']):>+11.1%}{g(L['voisins_moy']):>9}{g(L['voisins_pire']):>11}"
              f"{g(L['perf_fin']):>8}{L['creux']:>7.0%}")
    print("\ntri+ = trimestres positifs sur 4. Un reglage qui ne tient qu'a un trimestre,")
    print("qu'a une paire, ou que ses propres voisins contredisent, n'est pas un bord.")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
