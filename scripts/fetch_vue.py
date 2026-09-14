"""Recupere les bougies d'AFFICHAGE qui manquent aux paires du panier.

    python scripts/fetch_vue.py --tf 1h
    python scripts/fetch_vue.py --tf 1h --verifier   # ne telecharge rien

La page des simulations simule en cinq minutes mais AFFICHE en horaire, et c'est
la serie horaire qui definit la fenetre. Les dix paires du panier ont toutes
leur historique en cinq minutes ; seules BTC, ETH et SOL avaient l'horaire. Les
sept autres etaient donc mesurables et pas regardables.

POURQUOI TELECHARGER PLUTOT QU'AGREGER. Une bougie horaire est, en principe, la
somme des douze bougies de cinq minutes qu'elle contient. En principe seulement :
mesure faite sur BTC, ETH et SOL, qui ont les deux series, l'agregation retrouve
la cloture partout mais s'ecarte de l'ouverture sur seize a vingt-trois pour cent
des heures, jusqu'a neuf dixiemes de pour cent. Les deux series viennent de
recuperations differentes et ne coincident pas exactement. Agreger aurait donc
donne aux sept nouvelles paires un decor different de celui des trois anciennes,
sur la meme page, sans que rien ne le signale. On telecharge.

Le script ne touche qu'aux paires et aux pas de temps demandes, et il n'ecrase
jamais : upsert_candles complete. Les robots de paper trading lisent le cinq
minutes et ne sont pas concernes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.storage import Storage  # noqa: E402

PAS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h", choices=sorted(PAS))
    ap.add_argument("--paires", default=None, help="par defaut, le panier de liquidite")
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args()

    #  La fenetre est celle que la page annonce, heritee et jamais recalculee.
    meta = json.loads((RACINE / "docs/simulations.json").read_text(encoding="utf-8"))["meta"]
    t0, heures = int(meta["t0"]), int(meta["heures"])
    fin = t0 + heures * 3_600_000
    attendu = (fin - t0) // PAS[args.tf]

    if args.paires:
        paires = [p.strip() for p in args.paires.split(",")]
    else:
        paires = json.loads((RACINE / "docs/archives/liquidite.json")
                            .read_text(encoding="utf-8"))["retenues"]

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    manquantes = []
    print(f"  fenetre : {t0} -> {fin}  ({heures} h), {attendu} bougies de {args.tf} attendues")
    for s in paires:
        n = st._conn.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=? AND ts>=? AND ts<?",
            (s, args.tf, t0, fin)).fetchone()[0]
        etat = "complet" if n >= attendu else f"{n}/{attendu}"
        print(f"    {s:<10} {etat}")
        if n < attendu:
            manquantes.append(s)
    if not manquantes:
        print("  rien a recuperer")
        return 0
    if args.verifier:
        print(f"  a recuperer : {', '.join(manquantes)}")
        return 0

    x = Exchange(cfg, trading=False)
    for s in manquantes:
        rows = x.fetch_ohlcv_full(s, args.tf, t0)
        #  On ne garde que la fenetre : une bougie posterieure n'a rien a faire
        #  dans une page qui annonce quatre cents jours.
        rows = [r for r in rows if t0 <= r[0] < fin]
        n = st.upsert_candles(s, args.tf, rows)
        apres = st._conn.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=? AND ts>=? AND ts<?",
            (s, args.tf, t0, fin)).fetchone()[0]
        etat = "complet" if apres >= attendu else f"INCOMPLET {apres}/{attendu}"
        print(f"    {s:<10} {n} ecrites, {etat}", flush=True)
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
