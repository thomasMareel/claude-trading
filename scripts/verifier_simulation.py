"""Cherche les ventes manquees et les prix de vente faux, sur l'historique reel.

    python scripts/verifier_simulation.py --tf 5m

L'utilisateur signale deux doutes : des prix de vente qui lui semblent trop
hauts, et des moments de vente manques. Ce ne sont pas des impressions a
discuter, ce sont des proprietes verifiables. Ce script rejoue la strategie puis
repasse sur CHAQUE bougie pour verifier quatre invariants qui doivent tenir
partout, et il nomme la bougie fautive quand l'un cede.

  I1  une vente limite rend EXACTEMENT l'objectif net, ni plus ni moins ;
  I2  le prix de sortie vaut le prix de revient majore de l'objectif et des
      frais — s'il est plus haut, c'est le prix de revient qui est faux ;
  I3  aucune bougie ne passe au-dessus du prix de sortie pendant qu'on detient
      un lot sans qu'une vente ait lieu dans cette bougie ou avant ;
  I4  aucun barreau n'est traverse par le bas sans etre achete, tant qu'il reste
      du cash et que la descente n'est pas abandonnee.

Les exceptions LEGITIMES sont declarees ici, pas decouvertes apres coup :
  - la bougie d'ouverture d'une descente, quand vente_meme_bougie est faux ;
  - une bougie baissiere, ou la convention fait visiter le haut AVANT le bas :
    un achat fait au bas ne peut pas etre revendu au haut de la meme bougie ;
  - un stop arme, qui annule les ordres d'achat et remplace la vente limite.
Tout ce qui n'entre pas dans ces trois cases est un defaut du moteur.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.grille import Descente, Reglages, chemin_bougie, rejouer  # noqa: E402
from src.storage import Storage  # noqa: E402

CAS = [
    ("La grille seule", dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04,
                             depart_sous=0.02)),
    ("Objectif 2 %", dict(profondeur=0.50, paliers=8, ratio=1.7, objectif_net=0.02,
                          depart_sous=0.02)),
    ("Patient, 12 paliers", dict(profondeur=0.30, paliers=12, ratio=1.3, objectif_net=0.03)),
    ("Avec cliquet", dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04,
                          depart_sous=0.02, cliquet_pas=0.02, cliquet_retrait=0.015)),
]


def bougies(st: Storage, s: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (s, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def verifier(symbole: str, b: list[tuple], rg: Reglages, budget: float) -> dict:
    """Rejoue en parallele du moteur et compare, bougie par bougie."""
    r = rejouer(symbole, b, rg, budget)
    cycles, journal = r["cycles"], r["journal"]
    faits = {}                                   # ts -> evenements de cette bougie
    for e in journal:
        faits.setdefault(e.ts, []).append(e)

    #  I1 et I2 : sur les ventes limites, l'arithmetique doit tomber juste
    i1 = [c for c in cycles if c.sortie == "limite"
          and abs(c.gain_pct - rg.objectif_a(c.paliers)) > 1e-9]
    i2 = [c for c in cycles if c.sortie == "limite" and abs(
        c.prix_sortie - c.prix_revient * (1 + rg.objectif_a(c.paliers)) / (1 - rg.frais)) > 1e-6]

    #  I3 et I4 : on refait le chemin, en tenant l'etat nous-memes
    d = Descente(symbole, r["equity"] and rg.echelle(1.0, 1.0) and b[0][1], budget, rg)
    d = Descente(symbole, b[0][1], budget, rg)
    cash = budget
    manquees, ratees = [], []
    ouvertures = {c.ouvert_le for c in cycles}
    for ts, o, hh, lo, c in b:
        evs = faits.get(ts, [])
        vendu = any(e.genre == "vente" for e in evs)
        arme = any(e.genre == "cliquet" for e in evs)
        for extreme, monte in chemin_bougie(o, hh, lo, c):
            if monte:
                if (d.cumul_unites and not d.stop and rg.cliquet_pas is None
                        and hh >= d.prix_sortie and not vendu
                        and not (not rg.vente_meme_bougie and d.ouverte_le is not None
                                 and ts <= d.ouverte_le)):
                    #  reste l'exception de la bougie baissiere : le haut a ete
                    #  visite AVANT le bas, donc avant l'achat qui aurait permis
                    #  cette vente. Elle ne compte pas comme manquee.
                    manquees.append((ts, hh, d.prix_sortie, len(d.remplis)))
            else:
                for i, prix, euros in d.barreaux_a_poser():
                    if lo <= prix and cash >= euros - 1e-9:
                        if not any(e.genre == "achat" and e.palier == i for e in evs):
                            ratees.append((ts, prix, i))
        #  on rejoue les effets reels du moteur pour rester synchronise
        for e in evs:
            if e.genre == "achat":
                d.acheter(e.palier, e.prix, ts); cash -= e.euros
            elif e.genre == "abandon":
                d.abandonnee = True
            elif e.genre == "cliquet":
                d.stop = e.prix
            elif e.genre == "vente":
                cash += d.vendre(e.prix, au_marche=(rg.cliquet_pas is not None))["recu"]
                d = Descente(symbole, c, budget, rg)
        if (rg.suivre_hausse and not d.engagee
                and c > d.reference * (1 + rg.reancrage_min)):
            d = Descente(symbole, c, budget, rg)
    return {"cycles": len(cycles), "i1": i1, "i2": i2, "manquees": manquees, "ratees": ratees}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    data = {s: bougies(st, s, args.tf) for s in cfg.symbols}
    data = {s: b for s, b in data.items() if len(b) > 500}
    print(f"{len(data)} paires x {min(len(b) for b in data.values())} bougies {args.tf}\n")

    total = 0
    for nom, spec in CAS:
        rg = Reglages(frais=frais, mise_min=args.plancher, vente_meme_bougie=False,
                      suivre_hausse=True, **spec)
        print(f"=== {nom} — objectif {rg.objectif_net:.0%}"
              f"{', cliquet' if rg.cliquet_pas is not None else ''} ===")
        for s, b in data.items():
            v = verifier(s, b, rg, args.budget)
            n = len(v["i1"]) + len(v["i2"]) + len(v["manquees"]) + len(v["ratees"])
            total += n
            print(f"  {s:<9} {v['cycles']:>4} cycles | gain faux {len(v['i1']):>3}"
                  f" | prix de sortie faux {len(v['i2']):>3}"
                  f" | ventes manquees {len(v['manquees']):>4}"
                  f" | achats rates {len(v['ratees']):>4}")
            for ts, hp, ps, npal in v["manquees"][:3]:
                print(f"      vente manquee : haut {hp:.2f} >= sortie {ps:.2f}, {npal} paliers")
            for ts, prix, i in v["ratees"][:3]:
                print(f"      achat rate : barreau {i + 1} a {prix:.2f}")
        print()
    print(f"{'AUCUN DEFAUT' if not total else str(total) + ' ANOMALIES'} sur "
          f"{len(CAS)} reglages x {len(data)} paires")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
