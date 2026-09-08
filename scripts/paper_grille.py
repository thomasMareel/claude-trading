"""Fait tourner la grille en paper trading, en temps reel, sur le vrai marche.

    python scripts/paper_grille.py --once        # un cycle, pour verifier
    python scripts/paper_grille.py               # boucle continue

AUCUNE LOGIQUE DE DECISION N'EST ECRITE ICI. Le programme telecharge les
bougies closes, les accumule, et rappelle rejouer() sur la serie entiere a
chaque reveil. La position courante est donc, par construction, exactement
celle que le backtest aurait produite sur ces memes prix : il n'existe pas de
seconde implementation qui pourrait diverger de la premiere. Le cout est de
rejouer quelques milliers de bougies par minute, ce qui ne se mesure pas.

CE QUE CE PAPER TRADING APPORTE, et que quatre cents jours de rejeu ne
donnent pas : du HORS-ECHANTILLON. Les reglages ont ete choisis en balayant
une periode passee, puis mesures sur cette meme periode. Tout ce qui suit le
lancement est, lui, inconnu au moment du choix. C'est le seul juge honnete.

Aucune cle d'API n'est utilisee, aucun ordre n'est transmis : seules les
donnees publiques de marche sont lues.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.grille import Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

#  Le reglage retenu, et la raison de chaque choix. Rien ici ne doit changer
#  sans etre dit : un reglage modifie en cours de route rend la mesure nulle.
#
#  Les paires sont choisies sur la LIQUIDITE mesuree, jamais sur leurs
#  resultats passes. Le volume en euros etait connu avant la periode et ne dit
#  rien du rendement a venir ; trier sur la performance reviendrait a choisir
#  apres coup, ce qui vaut vingt a trente points d'illusion. DOGE est donc
#  retenue bien qu'elle ait perdu la moitie de sa valeur : c'est le prix d'une
#  regle honnete.
PAIRES = ["BTC/EUR", "ETH/EUR", "XRP/EUR", "SOL/EUR", "DOGE/EUR"]
BUDGET = 200.0          # 1000 EUR au total, repartis a parts egales
PAS = "5m"              # le pas auquel toute l'etude a ete menee

#  DEUX ECHELLES TOURNENT EN PARALLELE, sur les MEMES paires et le MEME budget.
#  Tout ce qui les separe est la forme de l'echelle : la comparaison est donc
#  lisible, ce qui ne serait plus vrai si l'une d'elles avait aussi un autre
#  panier ou une autre mise. Leurs departs different d'une heure — chacune part
#  a l'instant ou SON reglage a ete fige, jamais avant : reculer le depart de la
#  seconde la ferait juger sur une heure deja connue de qui l'a reglee.
#
#  "3paliers"  L'echelle profonde issue du balayage. Sur le panier choisi par
#              regle, c'est la seule famille restee positive : les echelles
#              courtes finissent immobilisees a cent pour cent. Elle passe les
#              quatre epreuves du banc (voisinage, paires, trimestres, trois
#              finesses de bougies). Contrepartie mesuree : des cycles tres
#              longs, loin des douze heures visees.
#
#  "8paliers"  Celle que vous decrivez : premier barreau plus bas, mises plus
#              petites, beaucoup de barreaux, objectif ramene a 2 % nets.
#              UNE CONTRAINTE LA DEFORME, et il faut la dire : le plancher de
#              12 EUR par ordre (config.yaml, min_order_value) impose, sur un
#              budget de 200 EUR reparti en 8 barreaux, une progression d'au
#              plus 1,20 — au-dela, les premiers barreaux tomberaient sous le
#              plancher et ne seraient jamais poses. Une vraie progression
#              geometrique a 8 barreaux (raison 1,6) demanderait 840 EUR sur la
#              SEULE paire. A 200 EUR par paire, "grosses mises en bas" et
#              "huit barreaux" ne peuvent pas coexister : c'est une mesure, pas
#              un choix. L'echelle ci-dessous est donc la plus progressive que
#              le plancher autorise.
PROFILS = {
    "3paliers": dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04,
                     depart_sous=0.02, suivre_hausse=True, vente_meme_bougie=False,
                     mise_min=12.0),
    "8paliers": dict(profondeur=0.50, paliers=8, ratio=1.20, objectif_net=0.02,
                     depart_sous=0.02, suivre_hausse=True, vente_meme_bougie=False,
                     mise_min=12.0),
}
REGLAGE = PROFILS["3paliers"]      # remplace par --profil, sans changer le defaut


def etat_json(chemin: Path) -> dict:
    if chemin.exists():
        return json.loads(chemin.read_text(encoding="utf-8"))
    return {"depuis": None, "vus": {}}


def bougies_locales(st: Storage, s: str, depuis: int) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles "
        "WHERE symbol=? AND timeframe=? AND ts >= ? ORDER BY ts",
        (s, PAS, depuis)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def un_cycle(cfg, st: Storage, x: Exchange, etat: dict, sortie: Path, verbeux: bool = True,
             reglage: dict | None = None) -> dict:
    reglage = reglage or REGLAGE
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    rg = Reglages(frais=frais, **reglage)
    maintenant = int(time.time() * 1000)

    #  On ne lit que des bougies CLOSES : la derniere en cours mentirait, et
    #  c'est le defaut le plus classique d'un robot qui tourne en direct.
    limite_close = maintenant - (maintenant % 300_000)
    for s in PAIRES:
        deja = st._conn.execute(
            "SELECT MAX(ts) m FROM candles WHERE symbol=? AND timeframe=?", (s, PAS)).fetchone()["m"]
        depuis = int(deja) + 300_000 if deja else etat["depuis"]
        if depuis and depuis < limite_close:
            try:
                lot = x.fetch_ohlcv(s, PAS, limit=300, since=depuis)
                lot = [r for r in lot if r[0] < limite_close]
                if lot:
                    st.upsert_candles(s, PAS, lot)
            except Exception as e:                       # noqa: BLE001
                print(f"  {s} : {type(e).__name__} {str(e)[:70]}", flush=True)

    lignes, total_eq, total_bud = [], 0.0, 0.0
    for s in PAIRES:
        b = bougies_locales(st, s, etat["depuis"])
        if len(b) < 3:
            continue
        r = rejouer(s, b, rg, BUDGET)
        u = resume(r)
        d = r["descente_en_cours"]
        total_eq += r["equity_finale"]
        total_bud += BUDGET
        #  ce qui est nouveau depuis le dernier reveil, pour le journal
        vus = etat["vus"].get(s, 0)
        neufs = [e for e in r["journal"] if e.ts > vus]
        if neufs:
            etat["vus"][s] = max(e.ts for e in neufs)
            for e in neufs:
                q = datetime.fromtimestamp(e.ts / 1000, timezone.utc).strftime("%d/%m %H:%M")
                print(f"  {q}  {s:<9} {e.genre:<8} {e.prix:>12.4f}  {e.euros:>8.2f} EUR"
                      + (f"  gain {e.gain:+.2f}" if e.genre == "vente" else ""), flush=True)
        lignes.append({
            "paire": s, "equity": round(r["equity_finale"], 2),
            "perf": round(u["perf_pct"], 6), "cycles": len(r["cycles"]),
            "engage": round(d.cumul_euros, 2), "barreaux": len(d.remplis),
            "reference": round(d.reference, 6), "prix": b[-1][4],
            "revient": round(d.prix_revient, 6), "sortie": round(d.prix_sortie, 6),
            "abandonnee": d.abandonnee, "bougies": len(b),
            "hold": round(b[-1][4] / b[0][1] * (1 - frais) ** 2 - 1, 6),
        })

    jours = (max((l["bougies"] for l in lignes), default=0) * 5) / 1440
    etat["dernier"] = maintenant
    resume_ = {
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "depuis": etat["depuis"], "jours": round(jours, 2),
        "budget_total": total_bud, "equity_total": round(total_eq, 2),
        "perf_total": round(total_eq / total_bud - 1, 6) if total_bud else 0.0,
        "hold_moyen": round(sum(l["hold"] for l in lignes) / len(lignes), 6) if lignes else 0.0,
        "reglage": {**reglage, "budget": BUDGET, "paires": PAIRES, "pas": PAS},
        "paires": lignes,
    }
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(resume_, indent=1), encoding="utf-8")
    if verbeux:
        print(f"  {jours:.2f} j | book {total_eq:.2f} / {total_bud:.0f} EUR "
              f"({resume_['perf_total']:+.2%}) | repere {resume_['hold_moyen']:+.2%}", flush=True)
    return resume_


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--profil", default="3paliers", choices=sorted(PROFILS),
                    help="quelle echelle faire tourner ; chacune a son propre journal")
    ap.add_argument("--intervalle", type=int, default=60, help="secondes entre deux reveils")
    ap.add_argument("--etat", default=None)
    ap.add_argument("--sortie", default=None)
    args = ap.parse_args()
    #  Le profil d'origine garde SES fichiers, au nom inchange : le processus
    #  qui tourne deja depuis le 8 septembre continue d'y ecrire sans rupture.
    suffixe = "" if args.profil == "3paliers" else f"_{args.profil}"
    args.etat = args.etat or f"docs/data/paper_grille{suffixe}_etat.json"
    args.sortie = args.sortie or f"docs/data/paper_grille{suffixe}.json"
    reglage = PROFILS[args.profil]

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    x = Exchange(cfg, trading=False)
    chemin = Path(args.etat)
    etat = etat_json(chemin)
    if etat["depuis"] is None:
        #  t0 : la premiere bougie close apres le lancement. Tout ce qui precede
        #  a servi a choisir les reglages et ne peut pas les juger.
        etat["depuis"] = int(time.time() * 1000) // 300_000 * 300_000
        chemin.parent.mkdir(parents=True, exist_ok=True)
        print(f"Depart du paper trading [{args.profil}] : "
              f"{datetime.fromtimestamp(etat['depuis']/1000, timezone.utc)}")
        print(f"  {len(PAIRES)} paires x {BUDGET:.0f} EUR = {len(PAIRES)*BUDGET:.0f} EUR")
        mises = Reglages(frais=0.001, **reglage).echelle(1.0, BUDGET)
        print("  mises, du haut vers le bas : "
              + ", ".join(f"{e:.0f}" for _, e in mises) + " EUR")
        print(f"  echelle : profondeur {reglage['profondeur']:.0%}, {reglage['paliers']} paliers, "
              f"x{reglage['ratio']}, objectif {reglage['objectif_net']:.0%}\n", flush=True)

    while True:
        try:
            un_cycle(cfg, st, x, etat, Path(args.sortie), reglage=reglage)
        except Exception as e:                           # noqa: BLE001
            print(f"cycle en erreur : {type(e).__name__} {str(e)[:120]}", flush=True)
        chemin.write_text(json.dumps(etat), encoding="utf-8")
        if args.once:
            break
        time.sleep(args.intervalle)
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
