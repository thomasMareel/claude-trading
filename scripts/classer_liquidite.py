"""Le classement de liquidite qui choisit les paires, ecrit et rejouable.

    python scripts/classer_liquidite.py

POURQUOI CE FICHIER EXISTE. Le panier du paper trading se choisit sur la
liquidite — un critere connu d'avance, jamais un rendement. Mais un critere qui
ne s'accompagne pas de sa mesure ne vaut rien : la premiere version de ce
classement tenait dans un commentaire, elle annoncait « vingt fois moins
liquides que la dixieme » la ou le rapport reel est de deux a trois, et elle
faisait entrer TRX/EUR en sixieme position sur la foi d'UN SEUL mois de volume
anormal. Un classement qu'on ne peut pas rejouer est une opinion.

CE QUE MESURE CE SCRIPT, et pourquoi ainsi. Le volume quote d'une bougie est
close x volume, le volume etant en unites de base. On le somme par jour, puis
on prend la MEDIANE des jours plutot que la moyenne : TRX/EUR a connu un mois a
26 M EUR entre deux mois a 2,3 M, et toute moyenne dont la fenetre chevauche ce
mois-la multiplie sa liquidite par dix. La mediane d'une fenetre longue ignore
ce genre d'accident sans rien avoir a decider.

La fenetre est COMMUNE a toutes les paires et DATEE dans le fichier de sortie :
sans fenetre commune, une paire suivie en direct aurait plus de jours qu'une
autre et le classement melangerait liquidite et duree d'observation.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

MS_JOUR = 86_400_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--jours", type=int, default=180,
                    help="profondeur de la fenetre commune")
    ap.add_argument("--combien", type=int, default=10, help="paires a retenir")
    ap.add_argument("--sortie", default="docs/archives/liquidite.json")
    args = ap.parse_args()

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cx.row_factory = sqlite3.Row
    #  fin = la derniere bougie que TOUTES les paires possedent, pour que la
    #  fenetre soit reellement commune
    fins = [r[0] for r in cx.execute(
        "SELECT MAX(ts) FROM candles WHERE timeframe='5m' GROUP BY symbol")]
    fin = min(fins)
    debut = fin - args.jours * MS_JOUR
    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731

    lignes = []
    for (s,) in cx.execute(
            "SELECT DISTINCT symbol FROM candles WHERE timeframe='5m' ORDER BY symbol"):
        rows = cx.execute(
            "SELECT ts/86400000 AS j, SUM(close*volume) AS q, COUNT(*) AS n "
            "FROM candles WHERE symbol=? AND timeframe='5m' AND ts>=? AND ts<=? "
            "GROUP BY j ORDER BY j", (s, debut, fin)).fetchall()
        #  un jour incomplet fausserait la mediane vers le bas : on n'en garde
        #  que les jours pleins a plus de 90 %
        jours = [float(r["q"] or 0) for r in rows if r["n"] >= 0.9 * 288]
        if len(jours) < args.jours * 0.5:
            lignes.append(dict(paire=s, jours=len(jours), mediane=None, moyenne=None))
            continue
        lignes.append(dict(paire=s, jours=len(jours),
                           mediane=stt.median(jours), moyenne=stt.mean(jours),
                           min=min(jours), max=max(jours)))
    cx.close()

    utilisables = [x for x in lignes if x["mediane"] is not None]
    utilisables.sort(key=lambda x: -x["mediane"])
    retenues = [x["paire"] for x in utilisables[:args.combien]]

    print(f"Fenetre commune : {q(debut)} -> {q(fin)} ({args.jours} jours)")
    print("Volume quote par jour (close x volume), mediane des jours pleins.\n")
    print(f"{'rang':>5}  {'paire':<10}{'mediane':>12}{'moyenne':>12}{'max/mediane':>13}"
          f"{'jours':>7}")
    for i, x in enumerate(utilisables, 1):
        r = x["max"] / x["mediane"] if x["mediane"] else 0
        marque = "  <- retenue" if i <= args.combien else "  ecartee"
        print(f"{i:>5}. {x['paire']:<10}{x['mediane']/1e3:>9.0f} k€"
              f"{x['moyenne']/1e3:>9.0f} k€{r:>13.0f}{x['jours']:>7}{marque}")
    for x in lignes:
        if x["mediane"] is None:
            print(f"       {x['paire']:<10} seulement {x['jours']} jours pleins : "
                  f"hors classement")

    if len(utilisables) > args.combien:
        d, s_ = utilisables[args.combien - 1], utilisables[args.combien]
        print(f"\n  Frontiere {args.combien}/{args.combien+1} : {d['paire']} "
              f"{d['mediane']/1e3:.0f} k€ contre {s_['paire']} {s_['mediane']/1e3:.0f} k€, "
              f"soit un rapport de {d['mediane']/s_['mediane']:.1f}.")
        if d["mediane"] / s_["mediane"] < 2:
            print("  Ce rapport est ETROIT : la composition du panier depend du choix de")
            print("  la fenetre, et il faut le dire plutot que de presenter l'exclusion")
            print("  comme evidente.")
    print("\n  colonne max/mediane : un rapport eleve signale un mois anormal, du genre")
    print("  de celui qui avait fait entrer TRX/EUR en sixieme position.")
    print("\nPaires retenues : " + ",".join(retenues))

    Path(args.sortie).parent.mkdir(parents=True, exist_ok=True)
    Path(args.sortie).write_text(json.dumps({
        "mesure_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fenetre": {"debut": debut, "fin": fin, "jours": args.jours,
                    "debut_iso": q(debut), "fin_iso": q(fin)},
        "methode": "mediane du volume quote journalier (close x volume), jours pleins a 90 %",
        "retenues": retenues, "classement": lignes,
    }, indent=1), encoding="utf-8")
    print(f"Ecrit dans {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
