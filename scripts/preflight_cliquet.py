"""Le cliquet peut-il rapporter ? Mesure faite AVANT de l'implementer.

    python scripts/preflight_cliquet.py --tf 1m

L'idee de l'utilisateur : au lieu de vendre au seuil, y poser un stop et le
remonter a chaque palier de rentabilite franchi, pour capturer les grosses
remontees. Le mecanisme a un cout CERTAIN et un gain INCERTAIN :

  cout certain    le retrait consenti sous chaque palier, plus le passage de
                  l'ordre limite maker a un stop au marche taker, plus le
                  glissement. Paye a CHAQUE cycle, y compris ceux qui ne montent
                  jamais d'un cran.
  gain incertain  les crans supplementaires, quand le cours continue de monter.

On peut trancher sans ecrire le mecanisme. Le moteur actuel dit exactement OU et
QUAND chaque vente a eu lieu, et l'historique fin dit ce que le prix a fait
ENSUITE. Il suffit donc de rejouer, depuis chaque sortie reelle, ce que le
cliquet aurait rendu, et de le comparer a ce que la vente ferme a rendu.

Ce n'est pas une simulation complete : apres une sortie, les deux versions
divergent (le cliquet sort plus tard, donc rouvre sa descente ailleurs). Mais
sur la question posee — le cliquet gagne-t-il ou perd-il sur une sortie donnee —
la mesure est exacte, et elle coute quelques secondes au lieu d'une journee.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.grille import Reglages, rejouer  # noqa: E402
from src.storage import Storage  # noqa: E402

#  Les cinq meilleurs reglages a la baisse, sur lesquels l'utilisateur veut
#  construire les cinq nouvelles versions.
BASES = [
    ("Le plus solide", dict(profondeur=0.50, paliers=6, ratio=1.7, objectif_net=0.04, depart_sous=0.02)),
    ("Patient", dict(profondeur=0.30, paliers=12, ratio=1.3, objectif_net=0.03)),
    ("Equilibre", dict(profondeur=0.30, paliers=12, ratio=1.3, objectif_net=0.02)),
    ("Solide et plus actif", dict(profondeur=0.50, paliers=10, ratio=1.4, objectif_net=0.02,
                                  reancrage_min=0.02)),
    ("Deux par semaine", dict(profondeur=0.20, paliers=8, ratio=1.5, objectif_net=0.01)),
]


def bougies(st: Storage, s: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (s, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def rejeu_cliquet(b: list[tuple], depart: int, revient: float, g0: float,
                  pas: float, retrait: float, taker: float, glissement: float,
                  plafond: int, execution: str) -> tuple[float, int]:
    """Ce que le cliquet aurait rendu depuis l'instant `depart`.

    Rend (rentabilite nette realisee, nombre de crans montes). L'armement et la
    montee des crans se lisent sur la CLOTURE : c'est ce qu'un robot qui se
    reveille une fois par bougie sait faire, sans supposer un ordre stop dormant
    au carnet que le systeme ne sait pas encore poser.

    execution decide du prix de sortie, et c'est LA seule hypothese qui change le
    signe du resultat. On la fait donc varier au lieu de la choisir :
      "optimiste"  au prix exact du stop, sans rien payer. Faux, mais c'est ce que
                   ferait un rejeu naif, donc le repere haut.
      "realiste"   au prix du stop moins le glissement, sauf si la bougie a OUVERT
                   sous le stop : c'est alors un vrai trou de cotation et on part
                   de l'ouverture. Borne dans les deux cas par le bas de la bougie.
      "pessimiste" au plus bas de la bougie. Suppose qu'on est toujours servi au
                   pire moment, ce qui n'arrive pas a chaque fois.
    """
    def rent(p: float) -> float:
        return p * (1 - taker) / revient - 1

    def prix_de(niveau: float) -> float:
        return revient * (1 + niveau) / (1 - taker)

    niveau = g0 - retrait                      # premier stop, pose des l'armement
    crans = 0
    fin = min(len(b), depart + plafond)
    for i in range(depart, fin):
        _, ouv, _, bas, clot = b[i]
        stop = prix_de(niveau)
        if bas <= stop:
            if execution == "optimiste":
                px = stop
            elif execution == "pessimiste":
                px = bas
            else:
                #  un trou se reconnait a l'ouverture DEJA sous le stop ; sinon le
                #  stop a ete traverse en seance et sert pres de son prix
                depart_prix = ouv if ouv <= stop else stop
                px = max(bas, depart_prix * (1 - glissement))
            return rent(px), crans
        r = rent(clot)
        if r >= g0 + pas:
            k = int((r - g0) // pas)
            if k > crans:
                crans = k
                niveau = g0 + k * pas - retrait
    return rent(b[fin - 1][4] * (1 - glissement)), crans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1m")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--taker", type=float, default=0.0015)
    ap.add_argument("--glissement", type=float, default=0.0005)
    ap.add_argument("--bases", type=int, default=5, help="combien de geometries tester")
    ap.add_argument("--jours-max", type=int, default=60, help="fenetre de suivi apres une sortie")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    maker = float(cfg.get("exchange.fee_rate", 0.001))
    data = {s: bougies(st, s, args.tf) for s in cfg.symbols}
    data = {s: b for s, b in data.items() if len(b) > 500}
    par_jour = {"1m": 1440, "5m": 288, "1h": 24}[args.tf]
    plafond = args.jours_max * par_jour
    pas_ms = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000}[args.tf]

    print(f"{len(data)} paires x {min(len(b) for b in data.values())} bougies {args.tf}. "
          f"maker {maker:.3%}, taker {args.taker:.3%}, glissement {args.glissement:.3%}.")
    print(f"Surcout certain d'un stop face a une vente limite : "
          f"{(args.taker - maker + args.glissement) * 100:.2f} point par sortie.\n")

    #  Trois axes, et le premier seuil en fait partie : l'utilisateur a demande
    #  que la mesure choisisse s'il vaut mieux armer a 2 % ou ailleurs. g0 change
    #  aussi le moteur de base (il decide quand un cycle se boucle), donc chaque
    #  valeur exige son propre rejeu — c'est ce qui coute le temps ici.
    SEUILS = (0.005, 0.01, 0.02, 0.03, 0.04, 0.06)
    PAS = (0.005, 0.01, 0.02, 0.04)
    RETRAITS = (0.002, 0.004, 0.008, 0.015, 0.025)

    for nom, spec in BASES[:args.bases]:
        print(f"=== {nom} — echelle prof {spec['profondeur']:.0%}, "
              f"{spec['paliers']} paliers, x{spec['ratio']} ===")
        print(f"{'seuil':>7}{'sorties':>9}{'pas':>7}{'retrait':>9}"
              f"{'ecart/cycle':>14}{'montes':>9}{'gain/cycle ferme':>18}")
        meilleur = None
        for g0 in SEUILS:
            base = {**spec, "objectif_net": g0}
            rg = Reglages(frais=maker, mise_min=args.plancher, vente_meme_bougie=False,
                          suivre_hausse=True, **base)
            sorties = []
            for s, b in data.items():
                t0 = b[0][0]
                r = rejouer(s, b, rg, args.budget)
                for c in r["cycles"]:
                    i = (c.ferme_le - t0) // pas_ms
                    if 0 <= i < len(b):
                        sorties.append((b, int(i), c.prix_revient))
            if not sorties:
                continue
            local = None
            for pas in PAS:
                for retrait in RETRAITS:
                    if retrait >= pas:
                        continue
                    gains, crans = [], []
                    for b, i, revient in sorties:
                        g, k = rejeu_cliquet(b, i, revient, g0, pas, retrait,
                                             args.taker, args.glissement, plafond, "realiste")
                        gains.append(g)
                        crans.append(k)
                    moy = sum(gains) / len(gains) - g0
                    part = sum(1 for k in crans if k > 0) / len(crans)
                    if local is None or moy > local[0]:
                        local = (moy, pas, retrait, part)
            if local:
                m, pas, retrait, part = local
                print(f"{g0:>6.1%}{len(sorties):>9}{pas:>7.1%}{retrait:>9.1%}"
                      f"{m:>+13.3%}{part:>9.0%}{g0:>17.1%}")
                if meilleur is None or m > meilleur[0]:
                    meilleur = (m, g0, pas, retrait)
        if meilleur:
            m, g0, pas, retrait = meilleur
            verdict = "GAGNE" if m > 0 else "PERD"
            print(f"  --> {verdict} : au mieux {m:+.3%} par cycle, seuil {g0:.1%}, "
                  f"pas {pas:.1%}, retrait {retrait:.1%}")
            print()
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
