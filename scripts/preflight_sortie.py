"""Couper la queue peut-il rapporter ? Mesure faite AVANT d'implementer.

    python scripts/preflight_sortie.py --tf 5m

Le moteur ne vend jamais a perte : prix_sortie est toujours au-dessus du prix de
revient. Une position qui part a la baisse attend donc indefiniment que le
marche revienne, et tout cycle clos realise exactement son objectif. Les mauvais
cycles ne sont pas mauvais, ils sont ETERNELS — d'ou une duree mediane de 7
heures pour une moyenne de 62, les treize pour cent de cycles depassant deux
jours portant a eux seuls quatre-vingt-six pour cent du temps immobilise.

Ramener la moyenne a douze heures suppose donc de sortir en perte, ou au mieux a
l'equilibre, sur une partie des cycles. Le marche ayant perdu trente pour cent
sur la fenetre, ce pari n'est pas gagne d'avance : sortir en perte cristallise ce
que la patience aurait efface.

CE QUE MESURE CE SCRIPT, sans ecrire une ligne du mecanisme. Pour chaque cycle
reel qui depasse la duree limite, il relit le prix a l'instant ou la limite tombe
et calcule ce qu'une sortie au marche aurait rendu la — tarif taker et
glissement compris. Puis il compare :

  ce qu'on perd   la difference entre le gain force et l'objectif que la
                  patience a fini par atteindre ;
  ce qu'on gagne  les cycles que la paire LIBEREE aurait produits pendant le
                  temps restant — rejoues pour de vrai, pas estimes.

POURQUOI LA PAIRE ET NON LE CAPITAL. Premiere mesure faite : sur les huit
reglages publies, trois paires et quatre cents jours, il n'y a eu ZERO achat
refuse faute de cash et ZERO bougie ou le cash ne suffirait pas au plus petit
barreau libre. La strategie n'est jamais a court d'argent, et liberer du capital
n'achete donc rien. Ce qu'un cycle bloque immobilise, c'est la PAIRE : tant que
le lot est detenu, suivre_hausse ne peut pas re-ancrer et aucune nouvelle
echelle ne s'ouvre. Le gain d'une coupe est donc exactement ce que la paire
aurait produit pendant le temps rendu, et cela se rejoue au lieu de s'estimer.

"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config  # noqa: E402
from src.grille import Reglages, rejouer  # noqa: E402
from src.storage import Storage  # noqa: E402
from exporter_simulations import REGLAGES, PLANCHER  # noqa: E402

MIN_PAR_PAS = {"1m": 1, "5m": 5, "1h": 60}


def bougies(st: Storage, s: str, tf: str) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (s, tf)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--taker", type=float, default=0.0015)
    ap.add_argument("--glissement", type=float, default=0.0005)
    ap.add_argument("--source", default="docs/simulations.json")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    d = json.loads(Path(args.source).read_text(encoding="utf-8"))
    HEURE = d["meta"]["pas"]
    t0h = d["meta"]["t0"]
    prix = {s: bougies(st, s, args.tf) for s in d["meta"]["paires"]}
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    budget = d["meta"]["budget"]
    d["reglages_par_nom"] = {r["nom"]: r for r in d["reglages"]}
    pas_ms = MIN_PAR_PAS[args.tf] * 60_000

    def prix_a(sym: str, heure_idx: float) -> float | None:
        """Cloture de la bougie fine qui contient cet instant."""
        ts = t0h + heure_idx * HEURE
        i = int((ts - prix[sym][0][0]) // pas_ms)
        if not (0 <= i < len(prix[sym])):
            return None
        return prix[sym][i][4]

    LIMITES = (6, 12, 24, 48, 96, 168)
    print(f"Sortie forcee au marche : taker {args.taker:.2%}, glissement "
          f"{args.glissement:.2%}. Prix lus au pas {args.tf}.")
    print("Le remplacement est REJOUE : on repose une echelle neuve a l'instant de la")
    print("coupe et on la laisse tourner jusqu'a l'heure ou le cycle patient s'est"
          " denoue.")
    print()

    for spec in REGLAGES:
        nom = spec["nom"]
        params = {k: v for k, v in spec.items() if k not in ("cle", "nom")}
        rg = Reglages(frais=frais, mise_min=PLANCHER, vente_meme_bougie=False, **params)
        cy = []
        for s in d["meta"]["paires"]:
            for c in d["reglages_par_nom"][nom]["sims"][s]["cycles"]:
                cy.append((s, c))
        if len(cy) < 10:
            continue
        journal = {s: d["reglages_par_nom"][nom]["sims"][s]["journal"]
                   for s in d["meta"]["paires"]}
        total = sum(c[4] for _, c in cy)
        print(f"=== {nom} — {len(cy)} cycles, gain total {total:.2f} EUR ===")
        print(f"{'limite':>8}{'coupes':>8}{'perte cristallisee':>21}"
              f"{'gain du remplacement':>23}{'solde':>11}{'duree moy.':>12}")
        for L in LIMITES:
            perte = remplace = 0.0
            coupes = 0
            for s, c in cy:
                if c[8] <= L:
                    continue
                i_coupe = int((t0h + (c[0] + L) * HEURE - prix[s][0][0]) // pas_ms)
                i_fin = int((t0h + c[1] * HEURE - prix[s][0][0]) // pas_ms)
                if not (0 <= i_coupe < i_fin < len(prix[s])):
                    continue
                #  La position A L'INSTANT DE LA COUPE, reconstruite depuis le
                #  journal : c[3] et c[9] sont l'investi et le revient A LA FIN
                #  du cycle. Les utiliser ici valorise une position qui n'etait
                #  pas encore constituee, et fait apparaitre des sorties forcees
                #  plus rentables que la patience — ce qui est impossible.
                euros = unites = 0.0
                for e in journal[s]:
                    if e[1] != 0 or not (c[0] <= e[0] <= c[1]):
                        continue
                    if e[0] > c[0] + L:
                        break
                    euros += e[4]
                    unites += e[4] / e[2] * (1 - frais)
                if unites <= 0:
                    continue
                coupes += 1
                px = prix[s][i_coupe][4]
                recu = unites * px * (1 - args.taker) * (1 - args.glissement)
                perte += (recu - euros) - c[4]
                #  la paire libre, rejouee pour de vrai sur le temps rendu
                tranche = prix[s][i_coupe:i_fin + 1]
                if len(tranche) > 2:
                    r = rejouer(s, tranche, rg, budget, trace=False)
                    remplace += r["equity_finale"] - budget
            solde = perte + remplace
            duree = sum(min(c[8], L) for _, c in cy) / len(cy)
            marque = "  <-" if solde > 0 else ""
            print(f"{L:>7}h{coupes:>8}{perte:>20.2f}€{remplace:>22.2f}€"
                  f"{solde:>+10.2f}€{duree:>11.1f}h{marque}")
        print()
    print("perte cristallisee = gain de la sortie forcee moins gain de la patience.")
    print("gain du remplacement = ce que l'echelle neuve a REELLEMENT rendu sur le")
    print("temps libere, position ouverte a la fin valorisee au marche.")
    print("solde positif = couper a cette limite rapporte plus que d'attendre.")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
