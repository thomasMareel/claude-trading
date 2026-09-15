"""Y avait-il seulement de quoi servir l'ordre ?

    python scripts/mesurer_execution.py
    python scripts/mesurer_execution.py --paliers 20 --ratio 1.3

Le rejeu sert un ordre limite ENTIEREMENT, INSTANTANEMENT, au prix exact, des que
le bas d'une bougie atteint son prix. Il ne regarde jamais s'il s'est echange
quoi que ce soit. Ce script mesure ce qu'il aurait fallu regarder.

---------------------------------------------------------------------------
POURQUOI MAINTENANT. La demande porte sur un budget de DIX MILLE euros par paire,
pour pouvoir essayer des echelles a vingt ou trente barreaux. Les mises croissent
geometriquement : a huit barreaux de raison 1,6, le barreau du bas porte 38 % du
budget. Passer de mille a dix mille euros multiplie donc par dix la taille de
l'ordre le plus gros de l'echelle, celui qui, justement, ne se pose que dans les
creux — c'est-a-dire quand le carnet est le plus mince.

CE QUI EST ARRETE AVANT DE MESURER. Quatre decisions.

1. LE VOLUME EST CONVERTI EN EUROS. ccxt rend le volume dans la monnaie de base ;
   multiplie par la cloture, il devient ce qui s'est echange en euros pendant la
   bougie. C'est la seule unite dans laquelle une mise et un volume se comparent.

2. PARTICIPATION MAXIMALE DE DIX POUR CENT. On suppose qu'un ordre peut prendre
   au plus un dixieme de ce qui s'echange. C'est une hypothese GENEREUSE : les
   regles usuelles de cout d'impact se situent entre cinq et dix pour cent, et
   au-dela l'ordre deplace lui-meme le prix qu'il vise. La choisir haute rend la
   conclusion plus difficile a atteindre, donc plus solide si elle est atteinte.

3. TROIS HORIZONS, ET LE VERDICT PORTE SUR CELUI QUE LE REJEU SUPPOSE. Le
   rejeu sert le barreau ENTIER A L'INTERIEUR D'UNE BOUGIE DE CINQ MINUTES : la
   mesure qui juge le modele est donc la part de fenetres de cinq minutes dont le
   volume suffisait. On publie aussi une heure et un jour, parce qu'un ordre
   limite ne disparait pas s'il n'est pas servi tout de suite — il dort et se
   remplit par morceaux — mais ces deux-la sont des lectures INDULGENTES : elles
   supposent que le prix reste sur le barreau pendant tout l'horizon, ce que les
   400 jours de chutes deja mesures disent faux (45 % des chutes s'arretent a
   -1 %). Elles bornent donc par le haut, et la borne utile est celle de cinq
   minutes.

   Une premiere version de ce script prenait pour mesure principale le temps
   d'absorption au volume MOYEN, et concluait que tout etait executable sous
   48 h — 17,7 h pour le pire cas. Le chiffre etait juste et la conclusion
   fausse : il supposait dix-sept heures de presence continue du prix sur un
   barreau que le marche traverse en minutes.

4. ON MESURE LE MARCHE, PAS LA STRATEGIE. Aucune de ces mesures ne depend d'un
   rejeu : elles ne diront pas combien d'ordres ont ete mal servis, seulement de
   combien le modele d'execution est optimiste, paire par paire. Borner l'erreur
   sur les rendements demanderait un rejeu a execution contrainte, qui reste a
   faire et qui est la suite naturelle de ce script.

CE QUE LE SCRIPT NE CORRIGE PAS. Il regarde les paires en EURO d'OKX. Les memes
monnaies en USDT sont souvent dix a cent fois plus liquides ; le probleme mesure
ici est celui du couple monnaie-EURO, pas celui de la monnaie.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

PAS_MS = 300_000
MS_JOUR = 86_400_000
PARTICIPATION = 0.10


def mises(budget: float, paliers: int, ratio: float) -> list[float]:
    """Les memes mises que le moteur : geometriques, sommant au budget."""
    p = [ratio ** i for i in range(paliers)]
    s = sum(p)
    return [budget * x / s for x in p]


def duree(txt: float) -> str:
    """Un temps d'absorption, dans l'unite ou il se lit."""
    if txt < 1:
        return f"{txt * 60:.0f} s"
    if txt < 90:
        return f"{txt:.0f} min"
    if txt < 48 * 60:
        return f"{txt / 60:.1f} h"
    return f"{txt / 1440:.0f} j"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--paliers", type=int, default=8)
    ap.add_argument("--ratio", type=float, default=1.6)
    ap.add_argument("--budgets", default="1000,10000")
    ap.add_argument("--jours", type=int, default=880)
    ap.add_argument("--sortie", default="data/etudes/execution.json")
    ap.add_argument("--herite", default="data/etudes/marche.json",
                    help="fichier dont la fenetre est reprise")
    args = ap.parse_args()

    budgets = [float(x) for x in args.budgets.split(",")]
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    panier = json.loads((RACINE / "docs/archives/liquidite.json")
                        .read_text(encoding="utf-8"))["retenues"]
    #  LA MEME FENETRE QUE L'ETUDE, heritee et jamais recalculee. Les robots de
    #  paper trading ecrivent des bougies en continu : « la derniere bougie
    #  commune » avance de cinq minutes toutes les cinq minutes. L'ecart est
    #  minuscule sur 880 jours, et c'est bien ce qui le rend dangereux — on
    #  comparerait une executabilite mesuree sur une fenetre a des rendements
    #  mesures sur une autre, sans que rien ne le dise.
    src = RACINE / args.herite
    if src.exists():
        m = json.loads(src.read_text(encoding="utf-8"))
        t0 = int(m["t0"])
        fin = t0 + int(m["n_blocs"]) * int(m["bloc_jours"]) * MS_JOUR
        print(f"  fenetre heritee de {args.herite}")
    else:
        fin = min(r[0] for r in cx.execute(
            "SELECT MAX(ts) FROM candles WHERE timeframe='5m' GROUP BY symbol"))
        t0 = fin - args.jours * MS_JOUR
        print(f"  {args.herite} absent : fenetre calculee sur les donnees")

    ech = {b: mises(b, args.paliers, args.ratio) for b in budgets}
    print(f"  echelle mesuree : {args.paliers} paliers, raison {args.ratio:g}")
    for b in budgets:
        print(f"    a {b:>6.0f} EUR : barreau du haut {ech[b][0]:>7.1f} EUR, "
              f"du bas {ech[b][-1]:>8.1f} EUR "
              f"({ech[b][-1] / b * 100:.0f} % du budget)")
    print(f"  participation supposee : {PARTICIPATION:.0%} de ce qui s'echange\n")

    out: dict[str, dict] = {}
    for s in panier:
        v = [float(a) * float(c) for a, c in cx.execute(
            "SELECT volume,close FROM candles WHERE symbol=? AND timeframe='5m' "
            "AND ts>=? AND ts<?", (s, t0, fin))]
        if not v:
            continue
        n = len(v)
        vt = sorted(v)
        par_min = sum(v) / (n * 5)
        #  Le volume cumule sur des fenetres disjointes de 1, 12 et 288 bougies :
        #  cinq minutes, une heure, un jour. Disjointes et non glissantes — une
        #  fenetre glissante compterait douze fois la meme bougie et gonflerait
        #  la part de fenetres servies sans qu'aucun euro de plus ne s'echange.
        fenetres = {}
        for nom, pas in (("5min", 1), ("1h", 12), ("1j", 288)):
            cum = [sum(v[i:i + pas]) for i in range(0, n - pas + 1, pas)]
            fenetres[nom] = {
                str(b): round(sum(1 for x in cum if x * PARTICIPATION >= ech[b][-1])
                              / len(cum), 5)
                for b in budgets}
        out[s] = {
            "bougies": n,
            #  LA MISE MAXIMALE SERVABLE : celle qu'au moins 5 % des bougies de
            #  cinq minutes pouvaient absorber. C'est l'inverse exact de la
            #  colonne « 5 min » — au lieu de demander « quelle part des bougies
            #  avale CETTE mise », on demande « quelle mise passe dans 5 % des
            #  bougies ». Le meme seuil, lu dans l'autre sens, et c'est ce
            #  nombre-la qui se compare directement a une echelle.
            "servable": round(vt[int(n * 0.95)] * PARTICIPATION, 2),
            "volume_nul": round(sum(1 for x in v if x == 0) / n, 4),
            "eur_median": round(vt[n // 2], 2),
            "eur_q90": round(vt[int(n * 0.9)], 2),
            "eur_par_jour": round(par_min * 1440, 2),
            "absorption_min": {str(b): round(ech[b][-1] / (par_min * PARTICIPATION), 1)
                               for b in budgets},
            "servies": fenetres,
        }
    cx.close()

    for b in budgets:
        print(f"\n  BARREAU DU BAS A {b:.0f} EUR ({ech[b][-1]:.0f} EUR) — "
              f"part des fenetres dont le volume suffisait")
        print(f"    {'paire':<7} {'EUR/jour':>12} {'vol nul':>8} "
              f"{'5 min':>9} {'1 heure':>9} {'1 jour':>9}   {'au volume moyen':>16}")
        for s_, d in sorted(out.items(), key=lambda x: -x[1]["eur_par_jour"]):
            f = d["servies"]
            print(f"    {s_.split('/')[0]:<7} {d['eur_par_jour']:>12,.0f} "
                  f"{d['volume_nul'] * 100:>7.1f}% "
                  f"{f['5min'][str(b)] * 100:>8.2f}% {f['1h'][str(b)] * 100:>8.2f}% "
                  f"{f['1j'][str(b)] * 100:>8.2f}%   {duree(d['absorption_min'][str(b)]):>16}")

    print("\n  LA COLONNE QUI JUGE LE MODELE EST CELLE DE CINQ MINUTES : c'est dans une")
    print("  bougie de cinq minutes que le rejeu sert le barreau en entier. Les colonnes")
    print("  d'une heure et d'un jour sont des lectures indulgentes, qui supposent que le")
    print("  prix reste sur le barreau tout ce temps.")

    #  Le seuil est ecrit avant d'avoir lu la colonne : sous un vingtieme des
    #  bougies, la quasi-totalite des achats du rejeu sont des achats que le
    #  marche n'aurait pas pu servir.
    SEUIL = 0.05
    print(f"\n  VERDICT, au seuil de {SEUIL:.0%} des bougies de cinq minutes :")
    for b in budgets:
        ok = [s_ for s_, d in out.items() if d["servies"]["5min"][str(b)] >= SEUIL]
        ko = [s_ for s_, d in out.items() if d["servies"]["5min"][str(b)] < SEUIL]
        print(f"    a {b:>6.0f} EUR — credible : "
              f"{', '.join(x.split('/')[0] for x in ok) or 'AUCUNE'}")
        print(f"    {'':>13}  fictif   : "
              f"{', '.join(x.split('/')[0] for x in ko) or 'aucune'}")

    sortie = RACINE / args.sortie
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps({
        "t0": t0, "fin": fin, "jours": args.jours,
        "paliers": args.paliers, "ratio": args.ratio,
        "participation": PARTICIPATION,
        "mises": {str(b): [round(x, 2) for x in ech[b]] for b in budgets},
        "paires": out,
    }, indent=1), encoding="utf-8")
    print(f"\n  ecrit dans {sortie.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
