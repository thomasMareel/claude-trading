"""Choisit un vrai cycle et l'exporte, pour que la page de methode ne dessine rien d'invente.

    python scripts/exporter_methode.py

La page qui explique la strategie pourrait etre illustree avec des chiffres
inventes, bien ronds, ou tout tombe juste. Ce serait plus lisible et cela
vaudrait moins que rien : un schema qui n'a jamais eu lieu n'explique pas un
mecanisme, il le decore.

CE SCRIPT VA DONC CHERCHER UN CYCLE REEL dans les neuf cents jours en base,
rejoue avec l'echelle qui tourne en paper trading, et l'exporte entier : les
bougies, chaque achat, la vente, et surtout les deux series qui font toute la
demonstration — le PRIX, et le PRIX DE REVIENT moyen du lot, qui descend plus
vite que lui a chaque barreau touche.

LE CRITERE DE CHOIX est ecrit ici et ne regarde pas le gain : on veut un cycle
de trois ou quatre barreaux, dont la VENTE a eu lieu SOUS le premier achat.
C'est le seul cas qui montre ce que la strategie a de contre-intuitif — le cours
n'a pas besoin de revenir la ou il etait, c'est le prix de sortie qui est
descendu a sa rencontre. Parmi ceux qui remplissent cette condition, on prend
celui ou l'ecart est le plus net, puisque c'est celui qui se voit le mieux.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer  # noqa: E402

MS_JOUR = 86_400_000
#  L'echelle du profil "8paliers_concentre", celle qui tourne sur dix paires.
REGLAGE = dict(profondeur=0.51, paliers=8, ratio=1.6, objectif_net=0.02,
               depart_sous=0.02, espacement="puissance", courbure=2.0,
               suivre_hausse=True, vente_meme_bougie=False, mise_min=12.0,
               abandon_sous=0.15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--paires", default="BTC/EUR,ETH/EUR,SOL/EUR")
    ap.add_argument("--sortie", default="docs/data/methode.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    t0 = d["t0"]
    t1 = t0 + d["n_blocs"] * d["bloc_jours"] * MS_JOUR
    rg = Reglages(frais=0.001, **REGLAGE)
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    candidats = []
    series = {}
    for s in args.paires.split(","):
        b = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
             for x in cx.execute(
                 "SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                 "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (s, t0, t1))]
        series[s] = b
        r = rejouer(s, b, rg, args.budget)
        j = r["journal"]
        for c in r["cycles"]:
            ach = [e for e in j if e.genre == "achat" and c.ouvert_le <= e.ts <= c.ferme_le]
            if not 3 <= len(ach) <= 4:
                continue
            if c.prix_sortie >= ach[0].prix:      # la vente doit etre SOUS le premier achat
                continue
            candidats.append((c.prix_sortie / ach[0].prix - 1, s, c, ach))
    cx.close()
    if not candidats:
        raise SystemExit("aucun cycle ne remplit le critere")
    candidats.sort(key=lambda x: x[0])
    ecart, paire, cyc, achats = candidats[0]

    #  La fenetre dessinee : le cycle, plus une marge de chaque cote pour qu'on
    #  voie d'ou le prix vient et ou il va apres.
    b = series[paire]
    marge = max(1, (cyc.ferme_le - cyc.ouvert_le) // 4)
    fen = [x for x in b if cyc.ouvert_le - marge <= x[0] <= cyc.ferme_le + marge]
    #  On agrege pour que le dessin reste lisible : viser environ 160 chandeliers.
    u = max(1, len(fen) // 160)
    bougies = []
    for i in range(0, len(fen), u):
        lot = fen[i:i + u]
        bougies.append([lot[0][0], lot[0][1], max(x[2] for x in lot),
                        min(x[3] for x in lot), lot[-1][4]])

    #  Les deux series qui portent la demonstration : a chaque instant, le prix de
    #  revient moyen du lot detenu et le prix auquel il partira. Reconstruites
    #  depuis le journal, pas recalculees a la main.
    revient, sortie = [], []
    euros = unites = 0.0
    k = 0
    for x in bougies:
        while k < len(achats) and achats[k].ts <= x[0]:
            euros += achats[k].euros
            unites += achats[k].euros / achats[k].prix * (1 - 0.001)
            k += 1
        if unites > 0 and x[0] <= cyc.ferme_le:
            pr = euros / unites
            revient.append([x[0], round(pr, 6)])
            #  meme formule que le moteur : revient x (1 + objectif) / (1 - frais)
            sortie.append([x[0], round(pr * (1 + rg.objectif_a(k)) / (1 - rg.frais), 6)])

    ech = rg.echelle(cyc.reference or achats[0].prix / (1 - REGLAGE["depart_sous"]),
                     args.budget)
    out = {
        "paire": paire, "budget": args.budget, "reglage": REGLAGE,
        "cycle": {
            "ouvert_le": cyc.ouvert_le, "ferme_le": cyc.ferme_le,
            "heures": round(cyc.heures, 1), "barreaux": cyc.paliers,
            "investi": round(cyc.investi, 2), "recu": round(cyc.recu, 2),
            "gain": round(cyc.gain, 2), "gain_pct": round(cyc.gain_pct, 6),
            "prix_revient": round(cyc.prix_revient, 6),
            "prix_sortie": round(cyc.prix_sortie, 6),
            "reference": round(cyc.reference, 6),
            "premier_achat": round(achats[0].prix, 6),
            "dernier_achat": round(achats[-1].prix, 6),
            "sous_premier_achat": round(ecart, 6),
        },
        "echelle": [{"prix": round(p, 6), "mise": round(e, 2),
                     "rempli": any(abs(a.euros - e) < 0.01 for a in achats)}
                    for p, e in ech],
        "achats": [{"ts": a.ts, "prix": round(a.prix, 6), "euros": round(a.euros, 2),
                    "revient": round(a.revient, 6)} for a in achats],
        "vente": {"ts": cyc.ferme_le, "prix": round(cyc.prix_sortie, 6),
                  "euros": round(cyc.recu, 2), "gain": round(cyc.gain, 2)},
        "bougies": [[x[0], round(x[1], 6), round(x[2], 6), round(x[3], 6), round(x[4], 6)]
                    for x in bougies],
        "revient": revient, "sortie": sortie,
    }
    Path(args.sortie).parent.mkdir(parents=True, exist_ok=True)
    Path(args.sortie).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")

    print(f"cycle retenu : {paire}, {cyc.paliers} barreaux, {cyc.heures:.0f} h")
    print(f"  premier achat a {achats[0].prix:.2f}, dernier a {achats[-1].prix:.2f}")
    print(f"  prix de revient moyen : {cyc.prix_revient:.2f}")
    print(f"  vendu a {cyc.prix_sortie:.2f}, soit {ecart:.2%} SOUS le premier achat")
    print(f"  investi {cyc.investi:.2f} EUR, gain {cyc.gain:+.2f} EUR "
          f"({cyc.gain_pct:+.2%} du lot)")
    print(f"  {len(bougies)} chandeliers, ecrit dans {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
