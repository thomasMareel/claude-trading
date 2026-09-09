"""Ou le capital passe-t-il ses 285 heures ? Au-dessus ou en dessous du revient ?

    python scripts/ou_passe_le_temps.py

LA QUESTION POSEE : « un stop suiveur se place un peu sous la courbe montante,
donc des que ca redescend, ca vend. Je ne vois pas comment l'argent peut rester
bloque aussi longtemps. »

C'est la bonne question, et elle vise un vrai defaut de ce qui a ete mesure. Le
cliquet ecrit dans le moteur ne s'ARME QUE SI la position est deja gagnante :
src/grille.py, armer_ou_monter, « if rent < g0: return False », ou g0 est
l'objectif net. Tant que le lot vaut moins que son prix de revient plus deux ou
quatre pour cent, il n'existe aucun stop. Le stop suiveur ne peut donc pas
couper une position perdante — la construction de Reglages l'interdit meme
explicitement, en refusant tout reglage dont le plancher ne serait pas un gain.

Or une grille achete PENDANT que le prix descend. A l'instant meme ou un barreau
est touche, la position est deja perdante, puisque le prix a continue de baisser
pour venir chercher ce barreau. Le temps bloque se passe donc tout entier dans
la zone ou le stop n'existe pas.

CE SCRIPT LE CHIFFRE, cycle par cycle, sur les 900 jours : combien d'heures un
lot passe sous son prix de revient, combien au-dessus, et jusqu'ou il est monte.
Si la reponse est « presque tout en dessous », alors le stop suiveur tel qu'il
est ecrit n'avait aucune chance d'agir sur la duree, et la mesure precedente
repondait a une autre question que celle posee.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer  # noqa: E402

MS_JOUR = 86_400_000
REGLAGE = dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04,
               depart_sous=0.02, suivre_hausse=True, vente_meme_bougie=False,
               mise_min=12.0, abandon_sous=0.15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--budget", type=float, default=200.0)
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    paires, n = d["paires"], d["n_blocs"]
    t0, t1 = d["t0"], d["t0"] + n * d["bloc_jours"] * MS_JOUR
    rg = Reglages(frais=0.001, **REGLAGE)
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    tous = []
    for s in paires:
        b = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
             cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND "
                        "timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (s, t0, t1))]
        r = rejouer(s, b, rg, args.budget)
        j = r["journal"]
        idx = {x[0]: i for i, x in enumerate(b)}
        for c in r["cycles"]:
            #  On rejoue la position du cycle bougie par bougie : le prix de
            #  revient change a chaque achat, donc « sous l'eau » n'est pas une
            #  question de prix absolu mais de prix RELATIF au revient du moment.
            achats = [e for e in j if e.genre == "achat" and c.ouvert_le <= e.ts <= c.ferme_le]
            if not achats:
                continue
            i0, i1 = idx.get(c.ouvert_le), idx.get(c.ferme_le)
            if i0 is None or i1 is None or i1 <= i0:
                continue
            sous = dessus = 0
            haut_rel = -1.0
            k = 0
            revient = achats[0].revient
            for x in b[i0:i1 + 1]:
                while k < len(achats) and achats[k].ts <= x[0]:
                    revient = achats[k].revient
                    k += 1
                if revient <= 0:
                    continue
                #  la bougie compte pour « au-dessus » des que son haut y passe
                if x[2] >= revient:
                    dessus += 1
                else:
                    sous += 1
                haut_rel = max(haut_rel, x[2] / revient - 1)
            tot = sous + dessus
            if not tot:
                continue
            tous.append(dict(paire=s, heures=c.heures, sous=sous * 5 / 60,
                             dessus=dessus * 5 / 60, part_sous=sous / tot,
                             haut=haut_rel, paliers=c.paliers, gain=c.gain))
    cx.close()
    if not tous:
        raise SystemExit("aucun cycle")

    longs = [x for x in tous if x["heures"] > 48]
    courts = [x for x in tous if x["heures"] <= 12]
    seuil = REGLAGE["objectif_net"]

    def bloc(nom, g):
        if not g:
            print(f"  {nom:<34} aucun")
            return
        h = [x["heures"] for x in g]
        print(f"  {nom:<34}{len(g):>6} cycles"
              f"{stt.median(h):>9.0f} h mediane"
              f"{sum(x['part_sous'] for x in g)/len(g):>10.0%} du temps sous le revient"
              f"{sum(x['sous'] for x in g)/len(g):>9.0f} h sous l'eau")

    print(f"Reglage du paper trading, {len(paires)} paires, {n * d['bloc_jours']} jours, "
          f"{args.budget:.0f} EUR par paire.")
    print(f"Le cliquet ne s'armerait qu'a +{seuil:.0%} NET au-dessus du prix de revient.\n")
    print("OU LE TEMPS SE PASSE")
    bloc("tous les cycles", tous)
    bloc("cycles de plus de 48 h", longs)
    bloc("cycles de 12 h ou moins", courts)

    #  Ne PAS compter les cycles qui n'atteignent jamais l'objectif : un cycle se
    #  ferme precisement quand il l'atteint, la reponse serait donc toujours zero
    #  et ne dirait rien. Ce qui compte est la part du temps passee SOUS le
    #  revient, la ou aucun stop n'est arme et ou rien ne peut couper.
    print()
    print("LE STOP SUIVEUR AURAIT-IL PU AGIR SUR LES CYCLES LONGS ?")
    print(f"  cycles de plus de 48 h : {len(longs)} sur {len(tous)}")
    print(f"  ils passent {sum(x['part_sous'] for x in longs)/len(longs):.0%} de leur temps "
          f"SOUS le prix de revient,")
    print(f"  soit {sum(x['sous'] for x in longs)/len(longs):.0f} heures en moyenne dans une "
          f"zone ou le cliquet n'existe pas :")
    print(f"  il ne s'arme qu'a +{seuil:.0%} NET AU-DESSUS du revient.")

    #  Et une fois arme, combien de temps le lot reste-t-il encore ouvert ?
    armes = [x for x in tous if x["haut"] >= seuil]
    print()
    print("APRES LE PREMIER PASSAGE AU-DESSUS DE L'OBJECTIF")
    print(f"  cycles qui y passent : {len(armes)}/{len(tous)} ({len(armes)/len(tous):.0%})")
    print(f"  ils durent en mediane {stt.median(x['heures'] for x in armes):.0f} h, "
          f"dont {stt.median(x['dessus'] for x in armes):.0f} h au-dessus du revient")
    print("  Un cycle qui touche l'objectif se solde presque aussitot : c'est deja")
    print("  ce que fait la vente ferme, et c'est pourquoi le cliquet n'ajoute rien.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
