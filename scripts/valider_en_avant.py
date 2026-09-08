"""Regle sur le passe, mesure sur l'avenir. Le seul test qui ne triche pas.

    python scripts/valider_en_avant.py --bloc 100 --apprentissage 3

TOUT CE QUI PRECEDE CE SCRIPT A ETE MESURE SUR LA PERIODE QUI A SERVI A LE
CHOISIR. Le banc de robustesse decoupe ce passe de quatre facons — par paire,
par trimestre, par voisinage, par finesse de bougie — mais ces quatre
decoupages portent sur LES MEMES JOURS. Un reglage peut les passer tous et
n'etre qu'une bonne adaptation a ce regime de marche precis.

CE QUE FAIT CE SCRIPT. Il decoupe l'historique en blocs de duree egale et
mesure CHAQUE reglage candidat sur CHAQUE bloc, echelle remise a neuf au
debut de chaque bloc. Cela produit une matrice reglage x bloc x paire, ecrite
telle quelle : toute lecture ulterieure se fait dessus, sans recalcul.

De cette matrice on tire deux chiffres qu'il ne faut jamais confondre :

  CHOISI DANS LE PASSE   a chaque bloc, on retient le reglage qui a le mieux
                         rendu sur les N blocs PRECEDENTS, et on releve ce
                         qu'il rend sur le bloc suivant, jamais vu. C'est la
                         seule estimation honnete de ce qu'on peut esperer.

  CHOISI APRES COUP      le reglage qui rend le mieux sur l'ensemble des
                         blocs, note sur ces memes blocs. C'est le chiffre
                         qu'on publie quand on ne fait pas attention, et
                         l'ecart entre les deux EST le surajustement.

CE QUE CE SCRIPT NE CORRIGE PAS. Les blocs viennent du meme marche et de la
meme decennie. Aucun decoupage ne fabrique un regime qui n'a pas eu lieu.
Une validation en avant elimine le surajustement a une periode ; elle ne
promet rien sur un futur qui ne ressemblerait a aucun bloc passe.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402

#  La grille de recherche. Large la ou les regimes se distinguent : une echelle
#  courte vit en marche calme et meurt en tendance, une echelle profonde
#  l'inverse. Si un reglage tient sur tous les blocs, ce sera visible.
GRILLE = dict(
    profondeur=(0.08, 0.15, 0.25, 0.40, 0.55),
    paliers=(3, 5, 8),
    ratio=(1.2, 1.6, 2.2, 3.0),
    objectif_net=(0.008, 0.015, 0.03, 0.06),
    depart_sous=(0.0, 0.02),
)
FIXE = dict(abandon_sous=0.15, suivre_hausse=True, vente_meme_bougie=False)

MS_JOUR = 86_400_000
PAS_MS = 300_000

#  Ces globales sont remplies une fois par processus fils : les series de prix
#  pesent trop pour etre reexpediees a chaque tache.
_BLOCS: dict = {}
_ARGS: dict = {}


def combos() -> list[dict]:
    cles = list(GRILLE)
    tout = [dict(zip(cles, v)) for v in itertools.product(*(GRILLE[k] for k in cles))]
    bons = []
    for p in tout:
        try:
            rg = Reglages(frais=_ARGS.get("frais", 0.001), mise_min=_ARGS.get("plancher", 12.0),
                          **FIXE, **p)
        except GrilleError:
            continue
        #  Un barreau sous le plancher de la plateforme ne serait jamais pose :
        #  l'echelle serait amputee sans qu'on le voie. On ecarte d'avance.
        if rg.echelle(1.0, _ARGS.get("budget", 200.0))[0][1] < _ARGS.get("plancher", 12.0):
            continue
        bons.append(p)
    return bons


def _init(chemin_db: str, paires: list[str], t0: int, bloc_ms: int, n_blocs: int, cfg: dict):
    """Charge les prix une fois par processus, decoupes en blocs par le TEMPS."""
    global _BLOCS, _ARGS
    _ARGS = cfg
    cx = sqlite3.connect(f"file:{chemin_db}?mode=ro", uri=True)
    attendu = bloc_ms // PAS_MS
    for s in paires:
        rows = cx.execute(
            "SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND timeframe='5m' "
            "AND ts>=? AND ts<? ORDER BY ts", (s, t0, t0 + n_blocs * bloc_ms)).fetchall()
        serie = [(int(a), float(o), float(h), float(l), float(c)) for a, o, h, l, c in rows]
        for k in range(n_blocs):
            d, f = t0 + k * bloc_ms, t0 + (k + 1) * bloc_ms
            #  Decoupe par horodatage et non par indice : une paire trouee
            #  glisserait sinon d'un bloc a l'autre sans qu'on s'en apercoive.
            tr = [x for x in serie if d <= x[0] < f]
            if len(tr) >= 0.8 * attendu:      # bloc trop troue : cellule ecartee
                _BLOCS[(s, k)] = tr
    cx.close()


def _tache(idx_et_params) -> list[tuple]:
    i, p = idx_et_params
    rg = Reglages(frais=_ARGS["frais"], mise_min=_ARGS["plancher"], **FIXE, **p)
    out = []
    for (s, k), b in _BLOCS.items():
        r = rejouer(s, b, rg, _ARGS["budget"], trace=False)
        u = resume(r)
        out.append((i, k, s, round(u["perf_pct"], 6), u["cycles"],
                    round(u["duree_moyenne_h"], 1), round(u["bloque_pct"], 4),
                    round(u["drawdown_max"], 5), round(u["part_temps_engage"], 4)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bloc", type=int, default=100, help="jours par bloc")
    ap.add_argument("--apprentissage", type=int, default=3, help="blocs de reglage")
    ap.add_argument("--budget", type=float, default=200.0)
    ap.add_argument("--plancher", type=float, default=12.0)
    ap.add_argument("--frais", type=float, default=0.001)
    ap.add_argument("--paires", default=None)
    ap.add_argument("--min-couverture", type=float, default=0.95,
                    help="part de la fenetre commune qu'une paire doit couvrir")
    ap.add_argument("--min-paires", type=int, default=4,
                    help="on n'accepte de raccourcir la fenetre que sous ce seuil")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--sortie", default="docs/validation-en-avant.json")
    ap.add_argument("--dry", action="store_true", help="annonce le plan et s'arrete")
    args = ap.parse_args()

    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    dispo = cx.execute(
        "SELECT symbol, COUNT(*) n, MIN(ts) a, MAX(ts) b FROM candles "
        "WHERE timeframe='5m' GROUP BY symbol").fetchall()
    voulu = set(args.paires.split(",")) if args.paires else None
    lignes = [(s, n, a, b) for s, n, a, b in dispo if voulu is None or s in voulu]
    if not lignes:
        raise SystemExit("aucune paire en base au pas 5m")

    #  Fenetre commune : LA PLUS LONGUE qui garde encore assez de paires. On ne
    #  troque pas des annees d'histoire contre quelques paires de plus — c'est
    #  precisement le manque d'histoire qui a rendu les reglages precedents
    #  suspects. Une paire dont la serie est trouee est ecartee, pas rabotee.
    lignes.sort(key=lambda x: x[2])
    fin = min(b for _, _, _, b in lignes)
    retenue = None
    for _, _, a, _ in lignes:
        gardees = [x for x in lignes if x[2] <= a and x[3] >= fin
                   and x[1] >= args.min_couverture * (fin - a) / PAS_MS]
        if len(gardees) >= args.min_paires:
            retenue = (a, gardees)
            break                     # lignes est trie : la premiere est la plus longue
    if retenue is None:
        raise SystemExit(f"aucune fenetre ne garde {args.min_paires} paires ; "
                         f"baisse --min-paires ou complete l'historique")
    t0, gardees = retenue
    paires = sorted(x[0] for x in gardees)
    ecartees = sorted(x[0] for x in lignes if x[0] not in paires)
    bloc_ms = args.bloc * MS_JOUR
    n_blocs = int((fin - t0) // bloc_ms)
    if n_blocs < args.apprentissage + 1:
        raise SystemExit(f"{(fin - t0) / MS_JOUR:.0f} jours : trop court pour "
                         f"{args.apprentissage + 1} blocs de {args.bloc} j")
    #  on cale la fin sur le present et on remonte : le dernier bloc, le plus
    #  recent, est celui qui ressemble le plus au marche a venir
    t0 = fin - n_blocs * bloc_ms
    cx.close()

    q = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    cfg = dict(frais=args.frais, budget=args.budget, plancher=args.plancher)
    global _ARGS
    _ARGS = cfg
    cbs = combos()
    total_brut = 1
    for v in GRILLE.values():
        total_brut *= len(v)
    print(f"{len(paires)} paires : {', '.join(paires)}")
    if ecartees:
        print(f"ecartees (historique trop court) : {', '.join(ecartees)}")
    print(f"fenetre {q(t0)} -> {q(fin)}  =  {n_blocs} blocs de {args.bloc} jours")
    print(f"{len(cbs)} reglages retenus sur {total_brut} (les autres poseraient un "
          f"barreau sous le plancher de {args.plancher:.0f} EUR)")
    print(f"{len(cbs) * n_blocs * len(paires)} rejeux a faire, {args.procs} processus\n",
          flush=True)
    if args.dry:
        return 0

    t_debut = time.time()
    faits = []
    with ProcessPoolExecutor(
            max_workers=args.procs, initializer=_init,
            initargs=(args.db, paires, t0, bloc_ms, n_blocs, cfg)) as ex:
        for j, lot in enumerate(ex.map(_tache, list(enumerate(cbs)), chunksize=1), 1):
            faits.extend(lot)
            if j % 10 == 0 or j == len(cbs):
                el = time.time() - t_debut
                print(f"  {j}/{len(cbs)} reglages — {el / 60:.1f} min, "
                      f"reste ~{el / j * (len(cbs) - j) / 60:.0f} min", flush=True)

    #  --- le repere : ne rien faire, bloc par bloc ---
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    hold = {}
    for s in paires:
        for k in range(n_blocs):
            d, f = t0 + k * bloc_ms, t0 + (k + 1) * bloc_ms
            r = cx.execute("SELECT open FROM candles WHERE symbol=? AND timeframe='5m' "
                           "AND ts>=? ORDER BY ts LIMIT 1", (s, d)).fetchone()
            r2 = cx.execute("SELECT close FROM candles WHERE symbol=? AND timeframe='5m' "
                            "AND ts<? ORDER BY ts DESC LIMIT 1", (s, f)).fetchone()
            if r and r2:
                hold[(s, k)] = float(r2[0]) / float(r[0]) * (1 - args.frais) ** 2 - 1
    cx.close()

    chemin = Path(args.sortie)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps({
        "t0": t0, "fin": fin, "bloc_jours": args.bloc, "n_blocs": n_blocs,
        "paires": paires, "budget": args.budget, "plancher": args.plancher,
        "fixe": FIXE, "reglages": cbs,
        "colonnes": ["reglage", "bloc", "paire", "perf", "cycles", "duree_h",
                     "bloque_pct", "creux", "part_engage"],
        "matrice": faits,
        "hold": [[s, k, round(v, 6)] for (s, k), v in hold.items()],
    }, separators=(",", ":")), encoding="utf-8")
    print(f"\nmatrice ecrite : {chemin}  ({len(faits)} cellules, "
          f"{(time.time() - t_debut) / 60:.1f} min)\n", flush=True)

    analyser(faits, cbs, paires, n_blocs, hold, args, q, t0, bloc_ms)
    return 0


def analyser(faits, cbs, paires, n_blocs, hold, args, q, t0, bloc_ms):
    """Toute la lecture se fait sur la matrice, sans un seul rejeu de plus."""
    #  moyenne sur les paires : un reglage se juge sur le panier, pas sur sa
    #  meilleure paire — sans quoi on rechoisirait la paire apres coup.
    par = defaultdict(list)
    for i, k, s, perf, cyc, dur, blq, dd, eng in faits:
        par[(i, k)].append((perf, cyc, dur, blq, dd, eng))
    moy = {ik: sum(x[0] for x in v) / len(v) for ik, v in par.items()}
    pire = {ik: min(x[0] for x in v) for ik, v in par.items()}
    cyc_m = {ik: sum(x[1] for x in v) / len(v) for ik, v in par.items()}
    dur_m = {ik: sum(x[2] for x in v) / len(v) for ik, v in par.items()}
    hold_b = {k: sum(hold[(s, k)] for s in paires if (s, k) in hold)
                 / max(1, sum(1 for s in paires if (s, k) in hold)) for k in range(n_blocs)}

    print("=" * 78)
    print("LE MARCHE, BLOC PAR BLOC (ne rien faire, moyenne du panier)")
    print("=" * 78)
    for k in range(n_blocs):
        d = q(t0 + k * bloc_ms)
        f = q(t0 + (k + 1) * bloc_ms)
        med = sorted(moy[(i, k)] for i in range(len(cbs)) if (i, k) in moy)
        mx = max(med) if med else 0.0
        print(f"  bloc {k}  {d} -> {f}   ne rien faire {hold_b[k]:+8.2%}   "
              f"| grille : mediane {med[len(med)//2]:+7.2%}, meilleure {mx:+7.2%}")

    def libelle(p):
        return (f"prof {p['profondeur']:>4.0%}  {p['paliers']} pal  x{p['ratio']:<3}  "
                f"obj {p['objectif_net']:>5.1%}  depart -{p['depart_sous']:.0%}")

    T = args.apprentissage
    print()
    print("=" * 78)
    print(f"CHOISI DANS LE PASSE — reglage retenu sur les {T} blocs precedents,")
    print("                       puis releve sur le bloc suivant, jamais vu")
    print("=" * 78)
    hon, hon_hold, retenus = [], [], []
    for k in range(T, n_blocs):
        cand = [i for i in range(len(cbs)) if all((i, j) in moy for j in range(k - T, k))]
        if not cand or (0, k) not in moy:
            continue
        #  critere de choix, fixe d'avance : rendement moyen sur les blocs
        #  d'apprentissage, penalise par le pire d'entre eux. Rien ici ne
        #  regarde le bloc de test.
        def note(i):
            v = [moy[(i, j)] for j in range(k - T, k)]
            return sum(v) / len(v) + 0.5 * min(v)
        best = max(cand, key=note)
        if (best, k) not in moy:
            continue
        appris = sum(moy[(best, j)] for j in range(k - T, k)) / T
        teste = moy[(best, k)]
        hon.append(teste)
        hon_hold.append(hold_b[k])
        retenus.append(best)
        print(f"  bloc {k}  {libelle(cbs[best])}")
        print(f"          appris {appris:+7.2%}  ->  teste {teste:+7.2%}   "
              f"(pire paire {pire[(best, k)]:+.2%}, {cyc_m[(best, k)]:.0f} cycles, "
              f"duree moy {dur_m[(best, k)]:.0f} h)")
        print(f"          ne rien faire sur ce bloc : {hold_b[k]:+.2%}")

    if not hon:
        print("  aucun bloc de test exploitable")
        return
    m_hon = sum(hon) / len(hon)
    m_hold = sum(hon_hold) / len(hon_hold)

    #  --- le chiffre malhonnete, pour mesurer l'ecart ---
    complet = [i for i in range(len(cbs)) if all((i, k) in moy for k in range(n_blocs))]
    apres = max(complet, key=lambda i: sum(moy[(i, k)] for k in range(n_blocs)))
    m_apres = sum(moy[(apres, k)] for k in range(T, n_blocs)) / (n_blocs - T)

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  blocs de test              : {len(hon)} x {args.bloc} jours")
    print(f"  CHOISI DANS LE PASSE       : {m_hon:+.2%} par bloc")
    print(f"  ne rien faire              : {m_hold:+.2%} par bloc")
    print(f"  CHOISI APRES COUP          : {m_apres:+.2%} par bloc  <- l'illusion")
    print(f"  surajustement mesure       : {m_hon - m_apres:+.2%} par bloc")
    print(f"  blocs de test positifs     : {sum(1 for x in hon if x > 0)}/{len(hon)}")
    print(f"  blocs battant le repere    : "
          f"{sum(1 for x, h in zip(hon, hon_hold) if x > h)}/{len(hon)}")
    c = Counter(retenus)
    print(f"  reglages differents retenus : {len(c)} pour {len(hon)} blocs")
    for i, n in c.most_common(3):
        print(f"      {n}x  {libelle(cbs[i])}")
    print("  Un reglage qui change a chaque bloc dit que la grille suit le bruit ;")
    print("  un reglage stable est un indice de mecanisme.")

    print()
    print("=" * 78)
    print("CE QUI TIENT SUR TOUS LES BLOCS (lecture apres coup, informative)")
    print("=" * 78)
    print(f"{'reglage':<52}{'pire bloc':>11}{'moyenne':>10}{'>0':>6}")
    solides = sorted(complet, key=lambda i: min(moy[(i, k)] for k in range(n_blocs)),
                     reverse=True)[:8]
    for i in solides:
        v = [moy[(i, k)] for k in range(n_blocs)]
        print(f"{libelle(cbs[i]):<52}{min(v):>+10.2%}{sum(v)/len(v):>+10.2%}"
              f"{sum(1 for x in v if x > 0):>4}/{len(v)}")
    print()
    print(f"  ne rien faire, pour comparaison : pire bloc "
          f"{min(hold_b.values()):+.2%}, moyenne "
          f"{sum(hold_b.values())/len(hold_b):+.2%}, "
          f"{sum(1 for x in hold_b.values() if x > 0)}/{n_blocs} blocs positifs")


if __name__ == "__main__":
    raise SystemExit(main())
