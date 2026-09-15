"""Ce que les tranches ETAIENT, avant toute strategie.

    python scripts/mesurer_marche.py
    python scripts/mesurer_marche.py --sortie data/etudes/marche.json

Aucun rejeu, aucun reglage : rien ici ne depend de la strategie. C'est ce qui
permet de s'en servir comme critere de CLASSE sans qu'un rendement n'entre
jamais dans le classement.

---------------------------------------------------------------------------
POURQUOI CE SCRIPT EXISTE A PART. La premiere version mesurait la volatilite
par la MEDIANE de l'amplitude des bougies de cinq minutes. Mesure faite sur les
880 jours : cette mediane vaut EXACTEMENT ZERO pour six paires sur dix — ADA,
LTC, LINK, TRX, DOGE, UNI — parce que plus de la moitie de leurs bougies de cinq
minutes sont plates, haut egal bas, pas une transaction. Un critere qui rend
zero pour six paires sur dix ne les classe pas : il les confond. Le defaut est
passe inapercu parce qu'une mediane est justement ce qu'on choisit pour se
proteger des valeurs extremes, et qu'on ne pense pas a la masse d'exactement
zero qui peut se trouver au milieu.

DEUX CRITERES PLUTOT QU'UN, ET LES DEUX SONT PUBLIES. Ils sont tous deux stables
— le classement tient d'une tranche a la suivante a +0,74 pour l'un et +0,72
pour l'autre — mais ILS NE CLASSENT PAS PAREIL, et c'est la le point :

  amplitude moyenne a cinq minutes   ETH 1re, SOL 2e, BTC 5e,  UNI 8e
  amplitude mediane a l'heure        DOGE 1re, SOL 2e, UNI 3e, ETH 6e, BTC 9e

L'ecart vient des paires qui traitent par a-coups : UNI est plate 76 % du temps
a cinq minutes, mais sur une heure elle bouge autant qu'ETH. Le premier critere
melange donc volatilite et LIQUIDITE ; le second regarde le seul deplacement du
prix, a l'echelle ou les cycles de la strategie se jouent.

Choisir l'un des deux d'apres les rendements serait exactement la faute que
toute cette etude cherche a eviter. On les publie donc tous les deux, le lecteur
refait ses classes sous chacun, et il dit si la conclusion change. Si elle ne
change pas, le choix du critere n'avait pas d'importance ; si elle change, c'est
un resultat et non un detail de methode.

LA PART DE BOUGIES PLATES EST PUBLIEE AVEC EUX, et ce n'est pas un ornement :
sur une bougie dont le haut egale le bas, aucun ordre limite n'a pu etre servi a
un autre prix, et le rejeu suppose pourtant qu'il l'a ete. C'est la principale
reserve sur les paires calmes, et elle se lit paire par paire.

LA DERIVE dit si la tranche montait ou descendait. La demande porte sur un bot
qui profite des petites resistances a la baisse DANS UN MARCHE EN BAISSE : sans
elle, un bon rendement moyen pourrait n'etre qu'un marche porteur.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

PAS_MS = 300_000
MS_JOUR = 86_400_000
BLOC_JOURS = 40
N_BLOCS = 22
PAR_HEURE = 12


def mesurer(cx, sym: str, debut: int, fin: int) -> list | None:
    """Les cinq nombres d'une tranche, ou None si la serie est trop trouee."""
    r = [(float(a), float(b), float(c)) for a, b, c in cx.execute(
        "SELECT high,low,close FROM candles WHERE symbol=? AND timeframe='5m' "
        "AND ts>=? AND ts<? ORDER BY ts", (sym, debut, fin))]
    if len(r) < 0.98 * (fin - debut) // PAS_MS:
        return None
    amp = [(h - l) / c for h, l, c in r if c > 0]
    #  L'heure est reconstituee a partir des douze bougies de cinq minutes. Le
    #  haut d'une heure EST le maximum des douze hauts et le bas le minimum des
    #  douze bas : contrairement a l'ouverture, ces deux-la sont exacts par
    #  agregation, ce qui evite d'avoir a telecharger une seconde serie.
    heures = []
    for i in range(0, len(r) - PAR_HEURE + 1, PAR_HEURE):
        g = r[i:i + PAR_HEURE]
        cl = g[-1][2]
        if cl > 0:
            heures.append((max(x[0] for x in g) - min(x[1] for x in g)) / cl)
    heures.sort()
    return [
        round(sum(amp) / len(amp), 8),                 # 0 amplitude moyenne a 5 min
        round(heures[len(heures) // 2], 8),            # 1 amplitude mediane a l'heure
        round(sum(1 for x in amp if x == 0) / len(amp), 4),   # 2 bougies plates a 5 min
        round(sum(1 for x in heures if x == 0) / len(heures), 4),  # 3 heures plates
        round(r[-1][2] / r[0][2] - 1, 5),              # 4 derive de la tranche
        len(r),                                        # 5 bougies mesurees
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--sortie", default="data/etudes/marche.json")
    ap.add_argument("--herite", default="data/etudes/balayage-1.json",
                    help="fichier de balayage dont la fenetre est reprise")
    args = ap.parse_args()

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    panier = json.loads((RACINE / "docs/archives/liquidite.json")
                        .read_text(encoding="utf-8"))["retenues"]
    dispo = {r[0]: (r[1], r[2]) for r in cx.execute(
        "SELECT symbol, MIN(ts), MAX(ts) FROM candles WHERE timeframe='5m' GROUP BY symbol")}
    #  LA FENETRE EST HERITEE, JAMAIS RECALCULEE. Les robots de paper trading
    #  ecrivent de nouvelles bougies en continu : « la derniere bougie commune »
    #  avance de cinq minutes toutes les cinq minutes, et deux scripts lances a
    #  une heure d'intervalle decoupent des tranches decalees. L'ecart est
    #  minuscule sur quarante jours et c'est bien ce qui le rend dangereux —
    #  rien ne l'aurait signale, et la tranche k d'un fichier n'aurait plus ete
    #  la tranche k de l'autre.
    src = RACINE / args.herite
    if src.exists():
        t0 = int(json.loads(src.read_text(encoding="utf-8"))["t0"])
        print(f"  fenetre heritee de {args.herite}")
    else:
        fin = min(dispo[s][1] for s in panier if s in dispo)
        t0 = (fin - N_BLOCS * BLOC_JOURS * MS_JOUR) // PAS_MS * PAS_MS
        print(f"  {args.herite} absent : fenetre calculee sur les donnees")
    bloc_ms = BLOC_JOURS * MS_JOUR

    out: dict[str, dict] = {}
    for s in panier:
        if s not in dispo:
            continue
        out[s] = {}
        for k in range(N_BLOCS):
            m = mesurer(cx, s, t0 + k * bloc_ms, t0 + (k + 1) * bloc_ms)
            if m:
                out[s][str(k)] = m
    cx.close()

    print(f"  fenetre {t0} -> {t0 + N_BLOCS * bloc_ms}, "
          f"{N_BLOCS} tranches de {BLOC_JOURS} jours")
    print(f"  {'paire':<7} {'tranches':>8} {'moy 5min':>10} {'med 1h':>9} "
          f"{'plates 5m':>10} {'plates 1h':>10} {'baissieres':>11}")
    for s, bl in sorted(out.items(), key=lambda x: -(
            sum(v[1] for v in x[1].values()) / len(x[1]) if x[1] else 0)):
        if not bl:
            continue
        v = list(bl.values())
        n = len(v)
        print(f"  {s.split('/')[0]:<7} {n:>8} "
              f"{sum(x[0] for x in v) / n * 100:9.4f}% {sum(x[1] for x in v) / n * 100:8.4f}% "
              f"{sum(x[2] for x in v) / n * 100:9.1f}% {sum(x[3] for x in v) / n * 100:9.1f}% "
              f"{sum(1 for x in v if x[4] < 0):>7}/{n}")

    sortie = RACINE / args.sortie
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps({
        "t0": t0, "n_blocs": N_BLOCS, "bloc_jours": BLOC_JOURS,
        "herite_de": args.herite if src.exists() else None,
        "criteres": ["amplitude moyenne a 5 minutes",
                     "amplitude mediane a l'heure"],
        #  paire -> tranche -> [moy 5m, med 1h, plates 5m, plates 1h, derive, bougies]
        "marche": out,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"  ecrit dans {sortie.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
