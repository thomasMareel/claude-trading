"""Charge un historique fin, pour supprimer l'hypothese de parcours intra-bougie.

    python scripts/fetch_fin.py --tf 5m --days 400
    python scripts/fetch_fin.py --tf 1m --days 400        # long : ~30 min

Le rejeu ne connait d'une bougie que quatre nombres. Il doit donc DEVINER dans
quel ordre le prix les a parcourus, et cette convention decide seule si un achat
touche avant une vente. Sur une bougie horaire c'est un pari lourd. Sur une
bougie d'une minute il ne reste presque rien a deviner : le meme moteur, nourri
plus finement, mesure la meme strategie avec beaucoup moins d'invention.

Le telechargement est repris la ou il s'est arrete : chaque page est ecrite
immediatement, et une relance repart de la derniere bougie en base. Une coupure
reseau au bout de vingt minutes ne fait donc rien perdre.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.storage import Storage  # noqa: E402

MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def charger(x: Exchange, st: Storage, symbole: str, tf: str, depuis: int, jusqu_a: int) -> int:
    """Pagine du plus ancien au plus recent, en ecrivant au fur et a mesure."""
    pas = MINUTES[tf] * 60_000
    curseur, total, vide = depuis, 0, 0
    while curseur < jusqu_a:
        try:
            lot = x.fetch_ohlcv(symbole, tf, limit=300, since=curseur)
        except Exception as e:                       # noqa: BLE001
            print(f"    {type(e).__name__}: {str(e)[:80]} — nouvelle tentative dans 5 s", flush=True)
            time.sleep(5)
            continue
        lot = [r for r in lot if depuis <= r[0] < jusqu_a]
        if not lot:
            #  Un trou dans l'historique ne doit pas arreter le chargement : on
            #  avance d'une page et on reessaie, jusqu'a trois pages vides.
            vide += 1
            if vide >= 3:
                break
            curseur += 300 * pas
            continue
        vide = 0
        total += st.upsert_candles(symbole, tf, lot)
        suivant = lot[-1][0] + pas
        if suivant <= curseur:                       # securite anti-boucle infinie
            break
        curseur = suivant
        if total % 30_000 < 300:
            fait = (curseur - depuis) / max(1, jusqu_a - depuis)
            print(f"    {symbole} {tf} : {total:>7} bougies, {fait:.0%}", flush=True)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m", choices=sorted(MINUTES))
    ap.add_argument("--paires", default=None,
                    help="liste separee par des virgules ; defaut : celles de config.yaml")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--fin", type=int, default=None,
                    help="horodatage ms de fin ; par defaut celui de l'historique 1h en base")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    x = Exchange(cfg, trading=False)

    #  On se cale exactement sur la fenetre de l'historique horaire deja en base,
    #  sans quoi les deux jeux ne seraient pas comparables.
    row = st._conn.execute(
        "SELECT MIN(ts) a, MAX(ts) b FROM candles WHERE symbol=? AND timeframe='1h'",
        (cfg.symbols[0],)).fetchone()   # fenetre de reference, toujours BTC/EUR
    jusqu_a = args.fin or (int(row['b']) + 3_600_000)
    depuis = jusqu_a - args.days * 86_400_000
    #  On ne se bride PLUS sur l'historique horaire deja en base : demander
    #  mille jours quand la base n'en contient que quatre cents doit remonter
    #  plus loin, pas se faire tronquer en silence.

    symboles = args.paires.split(",") if args.paires else cfg.symbols
    attendu = args.days * 1440 // MINUTES[args.tf]
    print(f"{len(symboles)} paires x ~{attendu} bougies {args.tf} "
          f"(~{attendu // 300 * len(symboles)} appels)", flush=True)
    t0 = time.time()
    pas_ms = MINUTES[args.tf] * 60_000
    for s in symboles:
        r = st._conn.execute(
            "SELECT MIN(ts) a, MAX(ts) b FROM candles WHERE symbol=? AND timeframe=?",
            (s, args.tf)).fetchone()
        #  Deux trous possibles, et le script n'en voyait qu'un : reprendre au
        #  dernier point connu ne remonte JAMAIS en arriere. Demander mille jours
        #  a une paire qui en a quatre cents ne rapportait donc rien du tout.
        trous = []
        if r["a"] is None:
            trous.append((depuis, jusqu_a))
        else:
            if depuis < int(r["a"]):
                trous.append((depuis, int(r["a"])))              # le passe manquant
            if int(r["b"]) + pas_ms < jusqu_a:
                trous.append((int(r["b"]) + pas_ms, jusqu_a))    # le present manquant
        if not trous:
            print(f"  {s:<10} deja complet", flush=True)
            continue
        n = sum(charger(x, st, s, args.tf, a, b) for a, b in trous)
        total = st.candle_count(s, args.tf)
        print(f"  {s:<10} +{n:>7} bougies {args.tf}  (total en base : {total})", flush=True)
    print(f"termine en {(time.time() - t0) / 60:.1f} min", flush=True)
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
