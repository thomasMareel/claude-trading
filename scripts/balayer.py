"""Le balayage : une geometrie d'echelle, rejouee sur vingt-deux tranches.

    python scripts/balayer.py --etape 1                  # le balayage complet
    python scripts/balayer.py --etape 1 --rapide         # une grille reduite, pour le tuyau
    python scripts/balayer.py --etape 1 --paires ETH/EUR # une seule paire

LA QUESTION POSEE. La methode de descente marche-t-elle sur toutes les monnaies,
ou seulement sur certaines ? Et si c'est « seulement certaines », lesquelles, et
selon quel critere connu D'AVANCE ?

Le critere candidat est la VOLATILITE. Il est le seul dont la persistance ait
ete mesuree sur ce banc : le classement des dix paires par amplitude tient d'une
tranche a la suivante a +0,77 de correlation de rang, quand le classement par
RENDEMENT tient a -0,10. Une classe de volatilite est donc une propriete qu'on
peut connaitre avant de choisir ; un classement de rendement ne l'est pas.

---------------------------------------------------------------------------
CE QUI EST ARRETE AVANT D'AVOIR VU LE MOINDRE CHIFFRE. Neuf decisions.

1. VINGT-DEUX TRANCHES DE QUARANTE JOURS, trois d'apprentissage, donc DIX-NEUF
   decisions en avant. Quarante jours est un choix de l'utilisateur ; il est
   assez long pour contenir plusieurs cycles et assez court pour que vingt-deux
   tranches tiennent dans l'historique.

2. LES CLASSES SONT DES CLASSES DE VOLATILITE, JAMAIS DE RENDEMENT, et elles se
   calculent sur les tranches DEJA VUES au moment de decider. Ce script ne
   classe rien : il mesure l'amplitude de chaque paire sur chaque tranche et la
   publie a cote des rendements. Le classement est fait par le lecteur, qui seul
   sait quelles tranches sont passees. Separer les deux est ce qui interdit au
   regard vers l'avenir d'entrer sans qu'on le voie.

3. DEUX CLASSES, pas trois. Les extremes sont nets — TRX derniere dans les
   vingt-deux tranches, ETH dans le haut presque partout — mais le milieu se
   promene de huit rangs sur dix. Un decoupage en trois inventerait une frontiere
   que la mesure ne soutient pas.

4. XRP EST GARDEE ET SIGNALEE. Son historique OKX commence le 2 aout 2025 : elle
   n'existe que dans une dizaine des vingt-deux tranches. Elle est donc mesuree
   comme les autres, publiee comme les autres, et EXCLUE DE TOUTE MOYENNE SUR LES
   PAIRES — sans quoi la moyenne porterait sur neuf paires avant sa date et sur
   dix apres, et changerait de sens au milieu du tableau. Sa fiche porte le
   nombre de tranches sur lesquelles elle a ete mesuree.

5. LE BUDGET EST UN AXE, PAS UN DECOR. Avec un plancher de douze euros par
   ordre, mille euros tuent la moitie d'une echelle a vingt barreaux et dix mille
   n'en tuent aucun : mesure faite, 10 barreaux vivants sur 20 a mille euros,
   17 sur 20 a dix mille. Les deux budgets sont donc rejoues, et le nombre de
   barreaux REELLEMENT POSES est publie pour chaque couple.

   MAIS LE SECOND BUDGET N'EST REJOUE QUE LA OU IL PEUT CHANGER QUELQUE CHOSE.
   Le plancher de la plateforme est le SEUL montant absolu du moteur : tout le
   reste — mises, caisse, valeur du lot — est proportionnel au budget, et
   perf_pct est un rapport. Quand aucun barreau n'est sous le plancher a mille
   euros, aucun ne l'est a dix mille et le rejeu est donc EXACTEMENT le meme,
   au rapport pres. Verifie plutot que suppose : sur huit combinaisons tirees au
   sort, l'ecart maximum entre les deux budgets est de 6e-15, soit le bruit du
   flottant ; et sur une combinaison dont quatre barreaux meurent, l'ecart est
   de 1,95 point. Les 282 combinaisons a echelle pleine sont donc rejouees une
   fois et recopiees ; les 201 autres sont rejouees deux fois. Le fichier de
   sortie porte les deux budgets en entier, comme si tout avait ete rejoue.

6. LA DUREE EST MESUREE, JAMAIS IMPOSEE. La seule condition de l'utilisateur est
   que les cycles durent moins de quarante-huit heures, dix en cible. Aucune
   combinaison n'est ecartee pour sa duree : on publie la MEDIANE, le neuvieme
   decile et la part des cycles sous quarante-huit heures, et le choix se fait
   ensuite en le sachant. Ecarter d'avance reviendrait a decider avant de voir.

7. LA DERIVE DE CHAQUE TRANCHE EST MESUREE. La demande vise un bot qui profite
   des petites resistances a la baisse DANS UN MARCHE EN BAISSE. Sans savoir
   quelles tranches montaient et lesquelles descendaient, un bon rendement moyen
   pourrait n'etre qu'un marche porteur. La derive est donc relevee par paire et
   par tranche, et le lecteur pourra tout relire sur les seules tranches
   baissieres.

8. JAMAIS DE VENTE A PERTE. abandon_sous reste a sa valeur du direct : sous le
   dernier barreau on cesse d'acheter, on ne vend pas. Le capital bloque en fin
   de tranche est publie tel quel, comme un cout et non comme une perte.

9. LE TEMOIN EST LE REGLAGE DU DIRECT. Il figure dans la grille de chaque etape,
   a la meme place que les autres : toute amelioration se lit comme un ecart a
   lui, et non dans l'absolu.

---------------------------------------------------------------------------
CE QUE CE SCRIPT NE FAIT PAS. Il ne choisit pas, il ne classe pas, il ne conclut
pas. Il rejoue et il ecrit. Le choix en avant, les classes, le temoin par tirage
au sort et le « ne rien faire » sont dans scripts/lire_balayage.py. Cette
separation est la meme que celle de l'etude de reference, et pour la meme
raison : un script qui mesure et conclut a la fois peut toujours etre soupconne
d'avoir mesure ce qu'il voulait conclure.

MEMOIRE. Une paire vaut 253 440 bougies de cinq minutes, soit 55 Mo par
processus. Charger les dix dans chacun des six processus demanderait 3,3 Go, sur
4,3 Go libres. On ouvre donc un pool PAR PAIRE : six processus portent la meme
paire, 330 Mo, et le pool est referme avant de passer a la suivante.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402
#  Le banc ne sait pas ce qu'il rejoue : le programme de chaque etape vit
#  dans plans.py. C'est ce qui permet d'ecrire l'etape suivante pendant que
#  la precedente tourne, sans toucher au fichier que les processus relisent.
from plans import AXES_1, BESOIN_VOLUME, PLANS, TEMOIN  # noqa: E402

PAS_MS = 300_000
MS_JOUR = 86_400_000
BLOC_JOURS = 40
N_BLOCS = 22
APPRENTISSAGE = 3
BUDGETS = (1_000.0, 10_000.0)

#  TEMOIN vient de plans.py : une seule definition, sinon les deux divergent.

# ---------------------------------------------------------------- rejeu
_BLOCS: list[list[tuple]] = []
_PRE: list[list[float]] = []
_CFG: dict = {}


def _init(chemin_db, paire, t0, n_pre, cfg):
    """Charge UNE paire, decoupee en tranches, une fois par processus."""
    global _CFG
    _CFG = cfg
    cx = sqlite3.connect(f"file:{chemin_db}?mode=ro", uri=True)
    bloc_ms = BLOC_JOURS * MS_JOUR
    colonnes = "ts,open,high,low,close" + (",volume" if cfg.get("volume") else "")
    serie = [tuple([int(r[0])] + [float(x) for x in r[1:]])
             for r in cx.execute(
                 f"SELECT {colonnes} FROM candles WHERE symbol=? "
                 "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                 (paire, t0 - n_pre * PAS_MS, t0 + N_BLOCS * bloc_ms))]
    cx.close()
    attendu = bloc_ms // PAS_MS
    for k in range(N_BLOCS):
        d = t0 + k * bloc_ms
        #  Tranche par HORODATAGE et non par indice : une paire trouee glisserait
        #  sinon d'une tranche a l'autre sans que rien ne le dise.
        tr = [x for x in serie if d <= x[0] < d + bloc_ms]
        _BLOCS.append(tr if len(tr) >= 0.98 * attendu else [])
        _PRE.append([x[4] for x in serie if x[0] < d][-n_pre:] if n_pre else [])


def _tache(arg) -> list[tuple]:
    i, spec, ib = arg
    spec = {k: v for k, v in spec.items() if not k.startswith("_")}
    rg = Reglages(frais=_CFG["frais"], **spec)
    budget = BUDGETS[ib]
    out = []
    for k, b in enumerate(_BLOCS):
        if not b:
            continue
        r = rejouer(_CFG["paire"], b, rg, budget, trace=False,
                    prechauffe=_PRE[k] or None)
        u = resume(r)
        h = sorted(c.heures for c in r["cycles"])
        n = len(h)
        out.append((
            i, ib, k,
            round(u["perf_pct"], 6),
            n,
            round(u["gain_pct"], 6),        # ce qui est REALISE, vente faite
            round(u["bloque_pct"], 5),      # ce qui dort encore en fin de tranche
            round(u["engage_moyen"], 4),
            round(u["drawdown_max"], 5),
            u["abandons"],
            round(h[n // 2], 2) if n else -1.0,                  # duree mediane
            round(h[min(n - 1, int(n * 0.9))], 2) if n else -1.0,  # neuvieme decile
            round(sum(1 for x in h if x < 48) / n, 4) if n else -1.0,
        ))
    return out


# ---------------------------------------------------------------- marche
def mesurer_marche(cx, paires, t0) -> dict:
    """Ce que la tranche etait, independamment de toute strategie.

    Trois nombres par paire et par tranche. L'AMPLITUDE MEDIANE est le critere
    de classe : mediane et non moyenne, parce qu'une seule bougie de krach
    deplacerait la moyenne d'une paire calme au niveau d'une paire agitee. LA
    PART DE BOUGIES PLATES borne le modele d'execution : sur une bougie dont le
    haut egale le bas, aucun ordre limite n'a pu etre servi a un autre prix, et
    le rejeu suppose pourtant qu'il l'a ete. LA DERIVE dit si la tranche montait
    ou descendait, seul moyen de relire l'etude sur les seuls marches en baisse.
    """
    bloc_ms = BLOC_JOURS * MS_JOUR
    out: dict[str, dict] = {}
    for s in paires:
        rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cx.execute(
            "SELECT ts,high,low,close FROM candles WHERE symbol=? AND timeframe='5m' "
            "AND ts>=? AND ts<? ORDER BY ts", (s, t0, t0 + N_BLOCS * bloc_ms))]
        par_bloc: dict[str, list] = {}
        for k in range(N_BLOCS):
            d = t0 + k * bloc_ms
            tr = [x for x in rows if d <= x[0] < d + bloc_ms]
            if len(tr) < 0.98 * bloc_ms // PAS_MS:
                continue
            amp = sorted((h - l) / c for _, h, l, c in tr if c > 0)
            par_bloc[str(k)] = [
                round(amp[len(amp) // 2], 7),
                round(sum(1 for _, h, l, _ in tr if h == l) / len(tr), 4),
                round(tr[-1][3] / tr[0][3] - 1, 5),
                len(tr),
            ]
        out[s] = par_bloc
    return out


def barreaux_vivants(spec: dict, budget: float) -> int:
    """Combien de barreaux la plateforme accepterait reellement (decision 5)."""
    rg = Reglages(**{k: v for k, v in spec.items() if not k.startswith("_")})
    return sum(1 for _, e in rg.echelle(100.0, budget) if e >= rg.mise_min)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--etape", type=int, default=1, choices=sorted(PLANS))
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--frais", type=float, default=0.001)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--paires", default=None)
    ap.add_argument("--rapide", action="store_true")
    ap.add_argument("--herite", default="data/etudes/balayage-1.json",
                    help="balayage dont la fenetre est reprise")
    ap.add_argument("--sortie", default=None)
    args = ap.parse_args()

    cbs = PLANS[args.etape](args.rapide, args.frais)
    #  Le prechauffage vaut la plus longue fenetre que le plan demande, toutes
    #  fenetres confondues : moyenne de reference, volatilite, filtre de moyenne
    #  et maximum glissant se servent tous des memes clotures precedentes.
    n_pre = max((max(int(c.get("fenetre_vol", 0)), int(c.get("filtre_moyenne", 0)),
                     int(c.get("devi_fenetre", 0)), int(c.get("moyenne_ref", 1)))
                 for c in cbs), default=0)
    if n_pre > 1:
        print(f"  prechauffage : {n_pre} bougies avant chaque tranche, "
              f"identiques pour toutes les fenetres")
    else:
        n_pre = 0

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    #  La fenetre finit a la derniere bougie commune et remonte de 880 jours :
    #  elle est definie par les DONNEES, jamais par une date ecrite a la main.
    panier = json.loads((RACINE / "docs/archives/liquidite.json")
                        .read_text(encoding="utf-8"))["retenues"]
    dispo = {r[0]: (r[1], r[2]) for r in cx.execute(
        "SELECT symbol, MIN(ts), MAX(ts) FROM candles WHERE timeframe='5m' GROUP BY symbol")}
    #  LA FENETRE EST HERITEE DE LA PREMIERE ETAPE, JAMAIS RECALCULEE. Les robots
    #  de paper trading ecrivent des bougies en continu : « la derniere bougie
    #  commune » avance de cinq minutes toutes les cinq minutes, et l'etape 1 a
    #  dure trois heures. Mesure faite : sans cet heritage, l'etape 2 partait
    #  3 h 24 plus tard que l'etape 1. Les comparaisons de ce banc sont
    #  APPARIEES — la meme geometrie, la meme paire, la MEME TRANCHE — et deux
    #  decoupages decales d'une poignee d'heures les auraient rendues fausses
    #  sans que rien ne le signale.
    src = RACINE / args.herite
    if src.exists():
        h = json.loads(src.read_text(encoding="utf-8"))
        t0 = int(h["t0"])
        if int(h["n_blocs"]) != N_BLOCS or int(h["bloc_jours"]) != BLOC_JOURS:
            raise SystemExit(
                f"{args.herite} decoupe {h['n_blocs']} x {h['bloc_jours']} j, "
                f"ce banc {N_BLOCS} x {BLOC_JOURS} j : refus de comparer a cote")
        print(f"  fenetre heritee de {args.herite}")
    else:
        fin = min(dispo[s][1] for s in panier if s in dispo)
        t0 = (fin - N_BLOCS * BLOC_JOURS * MS_JOUR) // PAS_MS * PAS_MS
        print(f"  {args.herite} absent : fenetre calculee sur les donnees")
    paires = [s for s in panier if s in dispo]
    if args.paires:
        paires = [p.strip() for p in args.paires.split(",")]
    partielles = [s for s in paires if dispo[s][0] > t0]

    print(f"  etape {args.etape} : {len(cbs)} combinaisons x {len(BUDGETS)} budgets "
          f"x {len(paires)} paires x {N_BLOCS} tranches de {BLOC_JOURS} j")
    for o in sorted({c["_origine"].split(":")[0] for c in cbs}):
        print(f"      {sum(1 for c in cbs if c['_origine'].startswith(o)):>4} {o}")
    print(f"  fenetre : {t0} -> {t0 + N_BLOCS * BLOC_JOURS * MS_JOUR}")
    if partielles:
        for s in partielles:
            manque = (dispo[s][0] - t0) // (BLOC_JOURS * MS_JOUR) + 1
            print(f"  {s} : historique partiel, ~{N_BLOCS - manque}/{N_BLOCS} tranches "
                  f"— gardee, mesuree, EXCLUE DES MOYENNES (decision 4)")

    t = time.perf_counter()
    marche = mesurer_marche(cx, paires, t0)
    cx.close()
    print(f"  marche mesure en {time.perf_counter() - t:.0f} s")

    #  Le second budget n'est rejoue que la ou le plancher de la plateforme mord
    #  (decision 5). Ailleurs, le rejeu est proportionnel et se recopie.
    vivants = [[barreaux_vivants(c, b) for b in BUDGETS] for c in cbs]
    neutres = {i for i, c in enumerate(cbs) if vivants[i][0] == c["paliers"]}
    taches = [(i, c, ib) for ib in range(len(BUDGETS)) for i, c in enumerate(cbs)
              if ib == 0 or i not in neutres]
    print(f"  {len(neutres)}/{len(cbs)} combinaisons ont toute leur echelle vivante a "
          f"{BUDGETS[0]:.0f} EUR : un seul rejeu, recopie (decision 5)")
    print(f"  {len(taches)} taches par paire au lieu de {len(cbs) * len(BUDGETS)}")
    lignes: list[tuple] = []
    t0_chrono = time.perf_counter()
    for ip, paire in enumerate(paires):
        cfg = {"frais": args.frais, "paire": paire,
               "volume": args.etape in BESOIN_VOLUME}
        tp = time.perf_counter()
        n0 = len(lignes)
        with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                                 initargs=(args.db, paire, t0, n_pre, cfg)) as ex:
            for res in ex.map(_tache, taches, chunksize=4):
                lignes.extend((ip,) + r for r in res)
        #  La recopie, faite ICI et nulle part ailleurs : le fichier de sortie
        #  porte les deux budgets en entier et le lecteur n'a rien a savoir.
        for x in [x for x in lignes[n0:] if x[2] == 0 and x[1] in neutres]:
            lignes.append((x[0], x[1], 1) + x[3:])
        d = time.perf_counter() - t0_chrono
        reste = d / (ip + 1) * (len(paires) - ip - 1)
        print(f"    {paire:<10} {len(lignes) - n0:>7} rejeux en "
              f"{time.perf_counter() - tp:>5.0f} s   (reste ~{reste / 60:.0f} min)",
              flush=True)
    print(f"  {len(lignes)} rejeux en {(time.perf_counter() - t0_chrono) / 60:.0f} min")

    sortie = RACINE / (args.sortie or f"data/etudes/balayage-{args.etape}.json")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps({
        "etape": args.etape, "t0": t0, "n_blocs": N_BLOCS, "bloc_jours": BLOC_JOURS,
        "apprentissage": APPRENTISSAGE, "budgets": list(BUDGETS), "frais": args.frais,
        "paires": paires, "partielles": partielles, "temoin": TEMOIN,
        #  Les axes ne decrivent que l'etape 1 : les etapes suivantes ne
        #  balayent pas une grille de valeurs mais des variantes appariees, et
        #  publier ici les axes d'une autre etape ferait croire a une surface
        #  qui n'a pas ete mesuree.
        "axes": ({k: [list(x) if isinstance(x, tuple) else x for x in v]
                  for k, v in AXES_1.items()} if args.etape == 1 else {}),
        "combinaisons": cbs,
        "vivants": vivants,
        "budget_neutres": sorted(neutres),
        #  paire -> tranche -> [amplitude mediane, part de bougies plates, derive, bougies]
        "marche": marche,
        #  [paire, combinaison, budget, tranche, perf, cycles, gain realise, bloque,
        #   engage moyen, pire creux, abandons, duree mediane, 9e decile, part < 48 h]
        "matrice": lignes,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"  ecrit dans {sortie.relative_to(RACINE)} "
          f"({sortie.stat().st_size / 1e6:.1f} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
