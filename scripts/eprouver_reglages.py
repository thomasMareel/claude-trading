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
  resolutions    le meme reglage rejoue en horaire, en cinq minutes et a la
                 minute doit rendre le meme nombre. C'est l'epreuve la plus
                 discriminante de toutes : a objectif 3 % on lit +9,37 / +9,30
                 / +9,11 %, a objectif 0,5 % on lit +6,65 / -0,56 / +6,62 %.
                 Sept points d'ecart selon la finesse des bougies ne mesurent
                 aucun bord, seulement une sensibilite au bruit.

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
    ap.add_argument("--resolutions", default="1h,5m,1m",
                    help="pas de temps compares pour l'epreuve de stabilite")
    ap.add_argument("--garder", type=int, default=25)
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--trimestres", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    entree = json.loads(Path(args.entree).read_text(encoding="utf-8"))
    #  Deux reglages qui rendent EXACTEMENT le meme resultat sont le meme
    #  reglage : un parametre inerte les distingue sur le papier seulement.
    #  abandon_sous s'est revele inerte sur les echelles profondes, ce qui
    #  remplissait le classement de triplets et gachait deux places sur trois.
    #  On deduplique sur le resultat mesure, jamais sur une liste de parametres
    #  supposes inertes : la mesure decide, pas moi.
    vus, candidats = set(), []
    for r in entree["classement"]:
        cle = (round(r.get("perf", 0), 9), r.get("cycles"), round(r.get("drawdown", 0), 9))
        if cle in vus:
            continue
        vus.add(cle)
        candidats.append(r["params"])
        if len(candidats) >= args.garder:
            break
    doublons = len(entree["classement"]) - len(vus)

    plein = {s: bougies(st, s, args.tf) for s in cfg.symbols}
    plein = {s: b for s, b in plein.items() if len(b) > 500}
    res_tf = [x for x in args.resolutions.split(",") if x]
    autres = {}
    for tf in res_tf:
        d = {s: bougies(st, s, tf) for s in cfg.symbols}
        d = {s: b for s, b in d.items() if len(b) > 500}
        if d:
            autres[tf] = d

    n = min(len(b) for b in plein.values())
    hold = sum((b[-1][4] / b[0][1]) * (1 - frais) ** 2 - 1 for b in plein.values()) / len(plein)
    print(f"{len(candidats)} candidats distincts. {len(plein)} paires x {n} bougies {args.tf}. "
          f"Ne rien faire : {hold:+.1%}")
    print("Resolutions comparees : "
          + ", ".join(f"{tf} ({len(d)} paires)" for tf, d in autres.items()))


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
        # --- stabilite selon la finesse des bougies
        res = {}
        for tf, d in autres.items():
            r = perf_sur(d, p, frais, args.budget, args.plancher)
            if r:
                res[tf] = r[0]
        ecart = max(res.values()) - min(res.values()) if len(res) > 1 else None
        lignes.append(dict(params=p, perf=base[0], pire_paire=base[1], cycles=base[2],
                           creux=base[3], trimestres=tri, paires=parp,
                           voisins_moy=sum(vs) / len(vs) if vs else None,
                           voisins_pire=min(vs) if vs else None,
                           resolutions=res, ecart_resolutions=ecart,
                           perf_fin=min(res.values()) if res else None))
        print(f"  {i}/{len(candidats)}", end="\r", flush=True)

    #  Le classement final ne recompense pas la performance mais la SOLIDITE :
    #  le pire trimestre et la pire paire pesent autant que la moyenne, et un
    #  reglage que ses propres voisins contredisent est ecarte.
    for L in lignes:
        pires = min(L["trimestres"]) + min(L["paires"])
        #  Le PIRE voisin, pas le voisin moyen : on ne regle jamais un parametre
        #  au point exact, et la question honnete est "si je me trompe d'un cran,
        #  qu'est-ce que j'obtiens ?". Une moyenne de voisins laisse passer un
        #  sommet dont un seul cote s'effondre.
        vois = L["voisins_pire"] if L["voisins_pire"] is not None else L["perf"]
        pire_res = L["perf_fin"] if L["perf_fin"] is not None else L["perf"]
        #  L'ecart entre resolutions est retranche a poids double : c'est la seule
        #  epreuve qui detecte un reglage dont le gain tient au hasard du parcours
        #  suppose a l'interieur de la bougie, et rien d'autre ne la remplace.
        instable = L["ecart_resolutions"] or 0.0
        L["solidite"] = (0.30 * L["perf"] + 0.25 * pires + 0.25 * vois + 0.20 * pire_res
                         + 0.5 * L["creux"] - 2.0 * instable)
        L["trim_positifs"] = sum(1 for t in L["trimestres"] if t > 0)
    lignes.sort(key=lambda L: -L["solidite"])

    sortie = Path(f"docs/epreuve-{Path(args.entree).stem}.json")
    sortie.write_text(json.dumps({"source": args.entree, "tf": args.tf, "hold": hold,
                                  "classement": lignes}, separators=(",", ":")), encoding="utf-8")
    print(f"\nEcrit dans {sortie}\n")
    print(f"{'prof':>5}{'pal':>4}{'ratio':>6}{'obj':>6}{'fond':>6}{'esp':>5}{'dep':>5}{'reanc':>6}"
          f"{'perf':>8}{'pire tri':>10}{'tri+':>6}{'pire paire':>11}"
          f"{'voisins':>9}{'pire vois':>11}{'ecart res':>11}{'creux':>7}")
    for L in lignes[:20]:
        p = L["params"]
        g = lambda v, f="{:>+7.1%}": "      -" if v is None else f.format(v)  # noqa: E731
        print(f"{p['profondeur']:>5.0%}{p['paliers']:>4}{p['ratio']:>6}{p['objectif_net']:>6.1%}"
              f"{('%.0f%%' % (p.get('objectif_profond', 0) * 100)) if p.get('objectif_profond') else '   -':>6}"
              f"{('geo' if p.get('espacement') == 'geometrique' else 'lin'):>5}"
              f"{p.get('depart_sous', 0):>5.0%}{p.get('reancrage_min', 0):>6.0%}"
              f"{L['perf']:>+8.1%}{min(L['trimestres']):>+10.1%}{L['trim_positifs']:>4}/4"
              f"{min(L['paires']):>+11.1%}{g(L['voisins_moy']):>9}{g(L['voisins_pire']):>11}"
              f"{g(L['ecart_resolutions'], '{:>10.1%}'):>11}{L['creux']:>7.0%}")
    print("")
    print("tri+ = trimestres positifs sur 4. ecart res = ecart entre le meilleur")
    print("et le pire resultat selon la finesse des bougies (" + ", ".join(autres) + ").")
    print("Un reglage qui ne tient qu'a un trimestre, qu'a une paire, que ses propres")
    print("voisins contredisent, ou qui change de reponse selon la finesse des")
    print("bougies, n'est pas un bord : c'est une coincidence trouvee a force de chercher.")

    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
