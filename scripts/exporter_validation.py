"""Prepare la page de la validation en avant : tout ce qu'elle affiche, en un JSON.

    python scripts/exporter_validation.py

La matrice brute de la validation pese sept cents kilo-octets : 280 reglages x
9 blocs x 5 paires. Une page ne la telechargera pas. Ce script en tire les
quelques dizaines de nombres qui portent la demonstration, et rejoue ce qui
manque — les reglages nommes, que la matrice ne contient pas parce qu'ils n'ont
jamais fait partie de la grille de recherche.

CE QUI EST CALCULE ICI, et rien d'autre :
  le verdict    les quatre moyennes qui se comparent : ce qu'un reglage choisi
                sur le passe rend, ce que rend ne rien faire, ce que rend le
                meilleur reglage designe apres coup, et l'ecart entre les deux
                derniers — qui EST le surajustement ;
  les blocs     pour chacun : ses dates, ce que ne rien faire a rendu, la
                mediane et le maximum de la grille ;
  le choix      a chaque bloc de test, quel reglage le passe designait et ce
                qu'il a rendu sur le bloc jamais vu ;
  les nommes    les reglages dont on parle, rejoues bloc par bloc, pour la
                carte de chaleur — c'est la ligne entierement verte du reglage
                a trois barreaux, face a celle qui alterne +13,9 et -31,3, qui
                se voit d'un coup d'oeil et qu'aucun tableau ne rend ;
  les boutons   le rang moyen de chaque valeur de chaque parametre, qui dit
                lesquels comptent et lesquels sont du bruit.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import Reglages, rejouer, resume  # noqa: E402

MS_JOUR = 86_400_000

#  Les reglages dont la page parle. Le premier est celui qui tourne en paper
#  trading : c'est LUI que la validation doit juger, pas un cousin arrondi aux
#  valeurs de la grille de recherche.
NOMMES = [
    ("Trois barreaux (paper trading)",
     dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04, depart_sous=0.02)),
    ("Huit barreaux, mises plates",
     dict(profondeur=0.50, paliers=8, ratio=1.20, objectif_net=0.02, depart_sous=0.02)),
    ("Echelle courte et frequente",
     dict(profondeur=0.12, paliers=4, ratio=1.8, objectif_net=0.015, depart_sous=0.0)),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="docs/validation-en-avant.json")
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--apprentissage", type=int, default=3,
                    help="blocs de reglage avant chaque bloc de test")
    ap.add_argument("--sortie", default="docs/data/validation.json")
    args = ap.parse_args()

    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    cbs, paires, n = d["reglages"], d["paires"], d["n_blocs"]
    t0, bloc_ms = d["t0"], d["bloc_jours"] * MS_JOUR
    #  La matrice ne conserve pas le nombre de blocs d'apprentissage : c'est un
    #  parametre de LECTURE, pas de mesure, et il se redonne ici. Trois est la
    #  valeur avec laquelle le verdict a ete publie.
    T = args.apprentissage
    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731

    par = defaultdict(list)
    for i, k, s, perf, cyc, dur, blq, dd, eng in d["matrice"]:
        par[(i, k)].append(perf)
    moy = {ik: sum(v) / len(v) for ik, v in par.items()}
    hold = defaultdict(list)
    for s, k, v in d["hold"]:
        hold[k].append(v)
    hold_b = {k: sum(v) / len(v) for k, v in hold.items()}

    def libelle(p):
        return (f"prof {p['profondeur']:.0%} · {p['paliers']} barreaux · "
                f"x{str(p['ratio']).replace('.', ',')} · obj {p['objectif_net']:.1%}")

    #  --- le choix bloc par bloc, exactement comme la validation l'a fait ---
    choix, hon, hon_hold = [], [], []
    for k in range(T, n):
        cand = [i for i in range(len(cbs)) if all((i, j) in moy for j in range(k - T, k))
                and (i, k) in moy]
        if not cand:
            continue
        def note(i):
            v = [moy[(i, j)] for j in range(k - T, k)]
            return sum(v) / len(v) + 0.5 * min(v)
        best = max(cand, key=note)
        appris = sum(moy[(best, j)] for j in range(k - T, k)) / T
        hon.append(moy[(best, k)])
        hon_hold.append(hold_b[k])
        choix.append(dict(bloc=k, reglage=libelle(cbs[best]), params=cbs[best],
                          appris=round(appris, 6), teste=round(moy[(best, k)], 6),
                          hold=round(hold_b[k], 6)))

    complet = [i for i in range(len(cbs)) if all((i, k) in moy for k in range(n))]
    apres = max(complet, key=lambda i: sum(moy[(i, k)] for k in range(n)))
    m_apres = sum(moy[(apres, k)] for k in range(T, n)) / (n - T)

    #  --- les reglages nommes, rejoues sur les memes blocs ---
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    series = {s: [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in
                  cx.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? "
                             "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                             (s, t0, t0 + n * bloc_ms))] for s in paires}
    cx.close()
    nommes = []
    for nom, p in NOMMES:
        rg = Reglages(frais=0.001, mise_min=d["plancher"], **d["fixe"], **p)
        ligne = []
        for k in range(n):
            vals = []
            for s in paires:
                b = [x for x in series[s]
                     if t0 + k * bloc_ms <= x[0] < t0 + (k + 1) * bloc_ms]
                if len(b) < 100:
                    continue
                vals.append(resume(rejouer(s, b, rg, d["budget"], trace=False))["perf_pct"])
            ligne.append(round(sum(vals) / len(vals), 6) if vals else None)
        v = [x for x in ligne if x is not None]
        nommes.append(dict(nom=nom, params=p, blocs=ligne,
                           moyenne=round(stt.mean(v), 6), pire=round(min(v), 6),
                           positifs=sum(1 for x in v if x > 0), total=len(v)))
        print(f"  {nom:<34} moyenne {stt.mean(v):+.2%}, pire {min(v):+.2%}", flush=True)

    #  --- ce que chaque bouton fait, sans designer de gagnant ---
    boutons = {}
    for cle in ("profondeur", "paliers", "ratio", "objectif_net", "depart_sous"):
        groupes = defaultdict(list)
        for k in range(n):
            cl = sorted((i for i in range(len(cbs)) if (i, k) in moy),
                        key=lambda i: moy[(i, k)], reverse=True)
            for r, i in enumerate(cl, 1):
                groupes[cbs[i][cle]].append(r / len(cl))
        boutons[cle] = [dict(valeur=v, rang=round(sum(g) / len(g), 4))
                        for v, g in sorted(groupes.items())]

    blocs = []
    for k in range(n):
        med = sorted(moy[(i, k)] for i in range(len(cbs)) if (i, k) in moy)
        blocs.append(dict(k=k, debut=q(t0 + k * bloc_ms), fin=q(t0 + (k + 1) * bloc_ms),
                          hold=round(hold_b[k], 6),
                          mediane=round(med[len(med) // 2], 6) if med else None,
                          meilleure=round(max(med), 6) if med else None))

    c = Counter(x["reglage"] for x in choix)
    out = dict(
        genere_le=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        paires=paires, n_blocs=n, bloc_jours=d["bloc_jours"], apprentissage=T,
        budget=d["budget"], n_reglages=len(cbs),
        verdict=dict(
            choisi_dans_le_passe=round(sum(hon) / len(hon), 6),
            ne_rien_faire=round(sum(hon_hold) / len(hon_hold), 6),
            choisi_apres_coup=round(m_apres, 6),
            surajustement=round(sum(hon) / len(hon) - m_apres, 6),
            blocs_testes=len(hon),
            positifs=sum(1 for x in hon if x > 0),
            battent_repere=sum(1 for x, h in zip(hon, hon_hold) if x > h),
            reglages_differents=len(c),
        ),
        blocs=blocs, choix=choix, nommes=nommes, boutons=boutons,
        champ_de_vision=dict(volatilite=0.20, chute_du_bloc=0.75, chute_suivante=-0.27),
    )
    Path(args.sortie).parent.mkdir(parents=True, exist_ok=True)
    Path(args.sortie).write_text(json.dumps(out, ensure_ascii=False,
                                            separators=(",", ":")), encoding="utf-8")
    v = out["verdict"]
    print(f"\nverdict : choisi dans le passe {v['choisi_dans_le_passe']:+.2%}, "
          f"ne rien faire {v['ne_rien_faire']:+.2%}, "
          f"apres coup {v['choisi_apres_coup']:+.2%}, "
          f"surajustement {v['surajustement']:+.2%}")
    print(f"ecrit dans {args.sortie} "
          f"({Path(args.sortie).stat().st_size / 1024:.0f} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
