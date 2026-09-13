"""Sur quoi accrocher l'echelle : la derniere cloture, ou une moyenne ?

    python scripts/etudier_reference.py            # le balayage complet
    python scripts/etudier_reference.py --rapide   # une grille reduite, pour verifier le tuyau

LA QUESTION POSEE. Le moteur pose toute son echelle sous un PRIX DE REFERENCE.
Depuis l'origine, c'est la derniere cloture au moment du re-ancrage, et le
premier barreau se pose a depart_sous en dessous. Deux choses se demandent donc
ensemble : sur COMBIEN de bougies calculer cette reference, et a quelle distance
sous elle declencher le premier achat.

---------------------------------------------------------------------------
CE QUI EST ARRETE AVANT D'AVOIR VU LE MOINDRE CHIFFRE. Sept decisions. Les
prendre apres aurait ete choisir le resultat.

1. UN BARREAU POSE AU-DESSUS DU COURS EST MORT. Des que la reference est une
   moyenne, elle passe au-dessus du prix pres d'une bougie sur deux, et les
   barreaux du haut se posent au-dessus du marche. Un ordre limite pose la
   n'attend pas : il part au marche, au tarif taker. Plutot que d'inventer
   cette execution, le moteur refuse le barreau (src/grille.py, champ plafond).
   Sans ce correctif, un marche parfaitement plat a 100 avec une reference a 120
   rendait -1,32 % : l'etude aurait mesure un artefact d'execution.

2. depart_sous SE BALAYE A BAS D'ECHELLE FIXE. haut = ref*(1-depart_sous) et
   bas = ref*(1-depart_sous-profondeur) descendent ENSEMBLE : balayer
   depart_sous a profondeur fixe, c'est balayer la profondeur, qui est le seul
   levier auquel ce banc reconnait un effet. On tient donc le bas a -51 % de la
   reference (profondeur = 0,51 - depart_sous) : seule l'ENTREE bouge. L'autre
   choix serait defendable ; il mesurerait autre chose, et il faut dire lequel
   on a pris.

3. LE RE-ANCRAGE COMPARE LA MOYENNE A L'ANCIENNE REFERENCE, jamais la cloture a
   la moyenne. La seconde lecture serait un signal de momentum — « le prix est
   au-dessus de sa moyenne mobile » — introduit en douce dans une etude qui
   pretend ne balayer qu'un parametre. reancrage_min reste a zero, sa valeur en
   direct.

4. LA MOYENNE EST PRECHAUFFEE. Les N clotures qui precedent le debut de chaque
   bloc sont chargees d'avance, identiques pour tous les N. Sans cela la fenetre
   grandirait de 1 a N pendant les premiers pas et chaque N se comporterait
   differemment au debut de chaque bloc — vingt-huit jours sur cent pour la plus
   longue. Et une moyenne calculee sur le bloc entier serait du regard vers
   l'avenir.

5. NEUF PAIRES, PRESENTES DANS LES NEUF BLOCS. XRP est ecartee pour COUVERTURE
   et non pour resultat : son historique OKX commence en aout 2025 et elle
   manquerait aux cinq premiers blocs. Un panier qui change en cours de route
   ferait porter la comparaison sur des choses differentes.

6. BUDGET DE MILLE EUROS, celui du direct. A deux cents euros, le premier
   barreau d'une echelle a huit barreaux de raison 1,6 vaut 2,80 EUR et serait
   refuse par le plancher de la plateforme : ce ne serait pas la meme famille.

7. LA REGLE DE CHOIX, ECRITE AVANT LES RENDEMENTS. Le choix en avant retient la
   combinaison dont la MOYENNE SUR LES PAIRES est la plus haute sur les trois
   blocs precedents — la meme regle que scripts/valider_en_avant.py, pour que
   les deux resultats se comparent.

---------------------------------------------------------------------------
CE QUE LE BALAYAGE NE POURRA PAS DIRE. Sur ce banc, le meilleur d'un balayage
cesse de valoir mieux qu'un tirage au sort vers deux cent quarante combinaisons :
la matrice de validation deja publiee le montre. Cette etude balaye large POUR
DESSINER LA SURFACE, ce qui est descriptif et legitime, et elle mesure separement
a partir de quelle taille de grille le choix cesse de payer. Nommer un vainqueur
n'est permis que si ce test l'autorise.

Le PLACEBO est dans la grille elle-meme : N = 1 balaye avec plusieurs
depart_sous EST « decaler l'echelle d'un montant constant ». Une moyenne ne
vaut quelque chose que si elle bat le meilleur decalage constant.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics as stt
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402

PAS_MS = 300_000
MS_JOUR = 86_400_000

#  L'echelle du direct, tout sauf ce qu'on balaye. Le bas reste a -51 % de la
#  reference : c'est profondeur + depart_sous, tenu constant (decision 2).
BAS = 0.51
FIXE = dict(paliers=8, ratio=1.6, objectif_net=0.02, espacement="puissance",
            courbure=2.0, abandon_sous=0.15, suivre_hausse=True,
            vente_meme_bougie=False, mise_min=12.0, reancrage_min=0.0)

#  N en bougies de cinq minutes. De l'instant au mois, par doublements : des
#  valeurs voisines donneraient la meme reference et gonfleraient le nombre de
#  tirages sans ajouter une seule strategie distincte.
N_BOUGIES = (1, 3, 6, 12, 24, 48, 96, 144, 288, 432, 576, 1152, 2016, 4032, 6048, 8064)
DEPARTS = (0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08)
N_RAPIDE = (1, 288, 2016)
DEPARTS_RAPIDE = (0.0, 0.02, 0.05)
MAX_N = max(N_BOUGIES)

_BLOCS: dict[tuple[str, int], list[tuple]] = {}
_PRE: dict[tuple[str, int], list[float]] = {}
_ARGS: dict = {}


def combos(ns, departs) -> list[dict]:
    out = []
    for n in ns:
        for ds in departs:
            spec = dict(FIXE, moyenne_ref=n, depart_sous=ds, profondeur=BAS - ds)
            try:
                Reglages(frais=0.001, **spec)
            except GrilleError:
                continue
            out.append(spec)
    return out


def _init(chemin_db, paires, t0, bloc_ms, n_blocs, cfg):
    """Charge une fois par processus : les blocs, et ce qui les precede."""
    global _ARGS
    _ARGS = cfg
    cx = sqlite3.connect(f"file:{chemin_db}?mode=ro", uri=True)
    debut = t0 - MAX_N * PAS_MS
    fin = t0 + n_blocs * bloc_ms
    attendu = bloc_ms // PAS_MS
    for s in paires:
        serie = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
                 for r in cx.execute(
                     "SELECT ts,open,high,low,close FROM candles WHERE symbol=? "
                     "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                     (s, debut, fin))]
        for k in range(n_blocs):
            d = t0 + k * bloc_ms
            f = d + bloc_ms
            #  Tranche par HORODATAGE et non par indice : une paire trouee
            #  glisserait sinon d'un bloc a l'autre.
            tr = [x for x in serie if d <= x[0] < f]
            if len(tr) < 0.98 * attendu:
                #  Plus severe que les 80 % de la validation en avant : une
                #  moyenne « sur N bougies » calculee sur une serie trouee est
                #  une moyenne sur une duree variable, invisible a N=1 et large
                #  de plusieurs jours a N grand.
                continue
            _BLOCS[(s, k)] = tr
            _PRE[(s, k)] = [x[4] for x in serie if x[0] < d][-MAX_N:]
    cx.close()


def _tache(arg) -> list[tuple]:
    i, spec = arg
    rg = Reglages(frais=_ARGS["frais"], **spec)
    budget = _ARGS["budget"]
    out = []
    for (s, k), b in _BLOCS.items():
        r = rejouer(s, b, rg, budget, trace=False, prechauffe=_PRE[(s, k)])
        u = resume(r)
        out.append((i, k, s,
                    round(u["perf_pct"], 6), u["cycles"],
                    round(u["gain_pct"], 6),          # ce qui est REALISE
                    round(u["bloque_pct"], 5),        # ce qui dort encore a la fin
                    round(u["part_temps_engage"], 4),
                    round(u["drawdown_max"], 5), u["abandons"]))
    return out


def artefact(cx, paires, t0, bloc_ms, n_blocs, ns) -> dict:
    """A quel point la moyenne decroche du prix, et dans quel sens.

    Ne depend ni de depart_sous ni du rejeu : c'est une propriete des seuls prix.
    On la mesure a part pour pouvoir dire, cellule par cellule, si un ecart de
    rendement est une strategie ou seulement un taux de participation qui change.
    """
    out: dict[str, dict] = {}
    fin = t0 + n_blocs * bloc_ms
    for s in paires:
        rows = [(int(r[0]), float(r[1])) for r in cx.execute(
            "SELECT ts,close FROM candles WHERE symbol=? AND timeframe='5m' "
            "AND ts>=? AND ts<? ORDER BY ts", (s, t0 - MAX_N * PAS_MS, fin))]
        ts = [x[0] for x in rows]
        cl = [x[1] for x in rows]
        cum = [0.0]
        for x in cl:
            cum.append(cum[-1] + x)
        i0 = next(i for i, t in enumerate(ts) if t >= t0)
        for n in ns:
            au_dessus = sup2 = tot = 0
            biais = 0.0
            for i in range(i0, len(cl)):
                m = (cum[i + 1] - cum[i + 1 - n]) / n
                e = m / cl[i] - 1
                biais += e
                tot += 1
                if e > 0:
                    au_dessus += 1
                if e > 0.02:
                    sup2 += 1
            out.setdefault(s, {})[n] = {
                "au_dessus": round(au_dessus / tot, 4),
                "au_dessus_2pct": round(sup2 / tot, 4),
                "biais_moyen": round(biais / tot, 6),
            }
    return out


def choisir(moy: dict, cbs: list[dict], blocs: list[int]) -> int:
    """La combinaison qui a le mieux rendu sur ces blocs. Regle ecrite d'avance."""
    def note(i):
        v = [moy[(i, k)] for k in blocs if (i, k) in moy]
        return sum(v) / len(v) if v else float("-inf")
    return max(range(len(cbs)), key=note)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--frais", type=float, default=0.001)
    ap.add_argument("--bloc", type=int, default=100)
    ap.add_argument("--apprentissage", type=int, default=3)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--rapide", action="store_true")
    ap.add_argument("--sortie", default="docs/etude-reference.json")
    args = ap.parse_args()

    ns = N_RAPIDE if args.rapide else N_BOUGIES
    departs = DEPARTS_RAPIDE if args.rapide else DEPARTS
    cbs = combos(ns, departs)

    #  Le calendrier est HERITE de la validation en avant : les deux etudes
    #  doivent porter sur les memes jours pour que leurs chiffres se comparent.
    src = RACINE / "docs/validation-en-avant.json"
    if not src.exists():
        raise SystemExit("docs/validation-en-avant.json absent ; "
                         "lance d'abord scripts/valider_en_avant.py")
    v = json.loads(src.read_text(encoding="utf-8"))
    t0, n_blocs, bloc_j = v["t0"], v["n_blocs"], v["bloc_jours"]
    bloc_ms = bloc_j * MS_JOUR

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    debut_utile = t0 - MAX_N * PAS_MS
    fin = t0 + n_blocs * bloc_ms
    dispo = {r[0]: (r[1], r[2]) for r in cx.execute(
        "SELECT symbol, MIN(ts), MAX(ts) FROM candles WHERE timeframe='5m' GROUP BY symbol")}
    panier = json.loads((RACINE / "docs/archives/liquidite.json")
                        .read_text(encoding="utf-8"))["retenues"]
    paires = [s for s in panier
              if s in dispo and dispo[s][0] <= debut_utile and dispo[s][1] >= fin - PAS_MS]
    ecartees = [s for s in panier if s not in paires]

    print(f"  {len(cbs)} combinaisons ({len(ns)} valeurs de N x {len(departs)} departs)")
    print(f"  {n_blocs} blocs de {bloc_j} jours, {len(paires)} paires : "
          f"{', '.join(p.split('/')[0] for p in paires)}")
    if ecartees:
        print(f"  ecartees pour COUVERTURE (pas pour resultat) : "
              f"{', '.join(p.split('/')[0] for p in ecartees)}")
    print(f"  prechauffage : {MAX_N} bougies avant chaque bloc, identique pour tous les N")

    t = time.perf_counter()
    art = artefact(cx, paires, t0, bloc_ms, n_blocs, ns)
    cx.close()
    print(f"  ecart moyenne/prix mesure en {time.perf_counter() - t:.0f} s")

    cfg = {"budget": args.budget, "frais": args.frais}
    lignes: list[tuple] = []
    t = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.procs, initializer=_init,
                             initargs=(args.db, paires, t0, bloc_ms, n_blocs, cfg)) as ex:
        for j, res in enumerate(ex.map(_tache, list(enumerate(cbs)), chunksize=1), 1):
            lignes.extend(res)
            if j % 10 == 0 or j == len(cbs):
                d = time.perf_counter() - t
                print(f"    {j}/{len(cbs)} combinaisons, {d:.0f} s "
                      f"(reste ~{d / j * (len(cbs) - j):.0f} s)", flush=True)
    print(f"  {len(lignes)} rejeux en {time.perf_counter() - t:.0f} s")

    sortie = RACINE / args.sortie
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps({
        "t0": t0, "n_blocs": n_blocs, "bloc_jours": bloc_j,
        "paires": paires, "ecartees": ecartees,
        "budget": args.budget, "frais": args.frais, "bas_echelle": BAS,
        "fixe": FIXE, "n_bougies": list(ns), "departs": list(departs),
        "apprentissage": args.apprentissage,
        "combinaisons": cbs,
        "artefact": {s: {str(n): d for n, d in v.items()} for s, v in art.items()},
        #  [combinaison, bloc, paire, perf, cycles, gain realise, bloque,
        #   part du temps engage, pire creux, abandons]
        "matrice": lignes,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"  ecrit dans {args.sortie} ({sortie.stat().st_size / 1e6:.1f} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
