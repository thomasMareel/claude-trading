"""Exporte les simulations de grille sous une forme tracable, puis fabrique la page.

    python scripts/exporter_simulations.py                 # ecrit docs/simulations.json
    python scripts/exporter_simulations.py --page          # ecrit aussi docs/simulations.html

Le resume chiffre d'un backtest dit COMBIEN. Il ne dit jamais OU, ni QUAND, ni
PENDANT COMBIEN DE TEMPS. Ce script sort la matiere qui manque : chaque achat et
chaque vente situes sur la courbe de prix, la part du budget engagee heure par
heure, et les segments pendant lesquels l'argent n'est pas ressorti.

COMPACITE. Un export naif ferait 25 Mo : 9600 heures x 3 paires x 8 reglages x
plusieurs series. Trois choix le ramenent a moins de deux :
  - les horodatages deviennent des indices dans une grille horaire reguliere,
    verifiee reguliere a l'export ;
  - le prix n'est exporte qu'une fois par paire, pas une fois par simulation ;
  - le prix de reference, le prix de revient et le prix de sortie ne changent
    qu'aux evenements du journal. On exporte les marches de l'escalier, pas
    l'escalier echantillonne heure par heure. La page les reconstruit.

Aucun secret ne transite ici : le fichier ne contient que des prix publics et
les nombres produits par le moteur.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

HEURE = 3_600_000
GENRE = {"achat": 0, "vente": 1, "abandon": 2, "cliquet": 3}

#  DEUX PAS DE TEMPS, et il faut les distinguer.
#  On SIMULE au pas fin : le moteur ne connait d'une bougie que quatre nombres
#  et doit deviner l'ordre du parcours ; sur une bougie horaire ce pari change
#  le resultat de plusieurs points. On AFFICHE au pas horaire : la courbe de
#  prix a la resolution fine peserait sept megaoctets par paire pour un trace
#  qui, sur quatre cents jours, ne montrerait pas un pixel de plus.
#  Les evenements gardent leur instant reel et sont ranges dans l'heure qui les
#  contient : un marqueur peut donc etre place jusqu'a une heure trop tot sur la
#  vue d'ensemble, jamais sur un autre jour.
PAS_SIM = "5m"
PAS_VUE = "1h"

#  Le plancher qui compte : la couche de risque reelle refuse tout ordre sous
#  max(risk.min_order_value, minimum de la plateforme). Avec une progression
#  geometrique forte, la moitie haute de l'echelle passe sous ce plancher et
#  n'aurait jamais ete posee. Rejouer sans ce plancher compte des operations de
#  deux centimes comme des trades : c'est la difference entre la strategie sur
#  le papier et la strategie executable.
PLANCHER = 12.0
PLANCHERS_COMPARES = (0.0, 5.0)

#  Les huit reglages compares. La page les classe ensuite par gain pur, mais
#  cette liste-ci raconte la recherche : le vainqueur des epreuves de robustesse
#  en tete, le vainqueur du classement brut garde exprès pour la comparaison, et
#  l'historique du projet en queue.
#
#  QUATRE MECANIQUES decident du resultat, et trois ont ete decouvertes en route :
#    suivre_hausse    reposer l'echelle sous le prix quand le marche monte a vide.
#                     Sans lui l'echelle reste plantee ou la derniere vente l'a
#                     laissee, et la grille s'endort pour de bon.
#    objectif_net     le frein principal. Viser 4 % impose d'attendre un rebond de
#                     4 % ; viser 0,5 % declenche plusieurs fois par jour mais ne
#                     survit pas au changement de finesse des bougies.
#    depart_sous      poser le premier barreau SOUS le prix et non dessus. Sans
#                     cela la grille achete le sommet de chaque micro-rebond.
#    ratio            une progression forte fait passer le haut de l'echelle sous
#                     le plancher de la plateforme, ce qui repousse le premier
#                     achat tres bas et rarefie les cycles.
#
#  abandon_sous, lui, s'est revele INERTE : a 15, 25 ou 40 % les resultats sont
#  identiques au centieme, parce que sur des echelles profondes le seuil n'est
#  jamais atteint. Il n'apparait donc plus ici.
#
#  vente_meme_bougie=False partout : on refuse de compter un aller-retour boucle
#  dans la bougie de son achat, car rien dans les donnees ne peut le prouver.
REGLAGES = [
    #  LA FAMILLE DEDUITE DES CHUTES. Rapide — quatre cycles par semaine, la
    #  meilleure cadence rentable mesuree — mais fragile : reduire sa profondeur
    #  d'un cinquieme la fait passer de +11 % a -6,5 %. Elle est reglee juste
    #  au-dela du point ou la plupart des chutes s'arretent, ce qui est puissant
    #  sur CE marche et ne se transporte pas.
    dict(cle="cadence-2", nom="Cadence, depart -2 %", profondeur=0.12, paliers=8, ratio=1.6,
         objectif_net=0.01, depart_sous=0.02, suivre_hausse=True),
    dict(cle="cadence-3", nom="Cadence, depart -3 %", profondeur=0.12, paliers=8, ratio=1.6,
         objectif_net=0.01, depart_sous=0.03, suivre_hausse=True),
    dict(cle="cadence-10", nom="Cadence, 10 paliers", profondeur=0.12, paliers=10, ratio=1.4,
         objectif_net=0.01, depart_sous=0.03, suivre_hausse=True),
    dict(cle="cadence-15", nom="Cadence, 15 % de profondeur", profondeur=0.15, paliers=10,
         ratio=1.4, objectif_net=0.01, depart_sous=0.03, suivre_hausse=True),
    #  LA FAMILLE PROFONDE, archivee mais gardee comme repere. Lente — un cycle
    #  toutes les trois semaines — mais tous ses voisins restent positifs, son
    #  creux vaut la moitie, et les trois finesses de bougies la donnent au meme
    #  nombre a quatre dixiemes pres.
    dict(cle="profonde-4", nom="Profonde et robuste", profondeur=0.50, paliers=3, ratio=3.3,
         objectif_net=0.04, depart_sous=0.02, suivre_hausse=True),
    dict(cle="profonde-3", nom="Profonde, objectif 3 %", profondeur=0.50, paliers=3, ratio=2.8,
         objectif_net=0.03, depart_sous=0.02, suivre_hausse=True),
    dict(cle="prudent-60-5", nom="Le moins risque", profondeur=0.60, paliers=5, ratio=2.4,
         objectif_net=0.08, depart_sous=0.04, reancrage_min=0.02, suivre_hausse=True),
    dict(cle="p08-14-r18-o2", nom="Le tableau d'origine", profondeur=0.08, paliers=14,
         ratio=1.8, objectif_net=0.02, suivre_hausse=False),

]


def arrondi(x: float, n: int = 6) -> float:
    """Coupe les decimales qui ne portent aucune information mais du poids."""
    return round(float(x), n)


def bougies(st: Storage, symbole: str, tf: str = "1h") -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (symbole, tf),
    ).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def grille_reguliere(b: list[tuple], pas: int) -> tuple[int, int]:
    """Verifie que les bougies forment bien un pas horaire regulier.

    Toute la compacite de l'export repose sur cette hypothese : si elle est
    fausse, les indices d'evenements designent la mauvaise heure et le graphique
    ment sans rien signaler. On refuse plutot que de tracer un mensonge.
    """
    t0 = b[0][0]
    for i, ligne in enumerate(b):
        if ligne[0] != t0 + i * pas:
            raise SystemExit(
                f"historique troue a l'indice {i} : attendu {t0 + i * HEURE}, trouve {ligne[0]}.\n"
                f"Relance scripts/fetch_history.py avant d'exporter."
            )
    return t0, len(b)


def segments_engages(dep: list[tuple], par_heure: int) -> list[list[int]]:
    """Les tranches d'heures pendant lesquelles de l'argent est immobilise.

    C'est la seule facon de donner une DUREE VECUE au blocage : un creux de
    deux cent vingt jours doit occuper deux cent vingt jours de largeur a
    l'ecran, pas une ligne dans un tableau.
    """
    segs, debut = [], None
    for i, (_, _, engage, _, _) in enumerate(dep):
        if engage > 1e-9 and debut is None:
            debut = i
        elif engage <= 1e-9 and debut is not None:
            segs.append([debut, i - 1])
            debut = None
    if debut is not None:
        segs.append([debut, len(dep) - 1])
    #  ramenes a la grille d'affichage horaire ; une bande d'une seule bougie
    #  fine reste visible en occupant son heure entiere
    return [[a // par_heure, b // par_heure] for a, b in segs]


def marches(suivi: list[tuple], champ: int, par_heure: int) -> list[list[float]]:
    """Une serie en escalier, reduite a ses marches : [indice, valeur].

    La reference, le revient et la sortie sont constants entre deux evenements.
    Les exporter heure par heure serait repeter neuf mille fois la meme valeur.
    """
    out: list[list[float]] = []
    prec = None
    for i, ligne in enumerate(suivi):
        v = arrondi(ligne[champ])
        if v != prec:
            out.append([i // par_heure, v])
            prec = v
    return out


def journalier(dep: list[tuple], eq: list[tuple], budget: float, par_jour: int) -> dict:
    """Le resume par jour, pour les vues d'ensemble.

    On garde le MAXIMUM d'engagement du jour, jamais la moyenne : une moyenne
    journaliere lisserait justement les pics d'engagement, qui sont le fait le
    plus important a montrer.
    """
    eng_max, eng_moy, equity, abandon = [], [], [], []
    for j in range(0, len(dep), par_jour):
        tranche = dep[j:j + par_jour]
        e = [x[2] / budget for x in tranche]
        eng_max.append(arrondi(max(e), 5))
        eng_moy.append(arrondi(sum(e) / len(e), 5))
        equity.append(arrondi(eq[j:j + par_jour][-1][1], 2))
        abandon.append(1 if any(x[4] for x in tranche) else 0)
    return {"engage_max": eng_max, "engage_moyen": eng_moy, "equity": equity, "abandon": abandon}


#  Les quatre epreuves, avec leurs seuils ecrits d'avance. Un reglage qui les
#  passe n'est pas garanti gagnant : il est seulement debarrasse des faux
#  positifs les plus courants. Les seuils sont severes et arbitraires, mais fixes
#  avant d'avoir vu les resultats, ce qui est la seule chose qui compte.
SEUILS = {
    "voisinage": "le pire voisin immediat reste positif",
    "paires": "les trois paires sont positives",
    "trimestres": "au moins 3 trimestres sur 4 positifs",
    "resolutions": "moins de 2 points d'ecart entre bougies de 1 h, 5 min et 1 min",
}
#  De combien on bouge chaque parametre pour sonder le voisinage.
VOISINAGE = {"profondeur": (0.8, 1.25), "objectif_net": (0.75, 1.33), "ratio": (0.92, 1.08)}


def _perf(data: dict, rg: Reglages, budget: float) -> list[float]:
    return [resume(rejouer(s, b, rg, budget, trace=False))["perf_pct"] for s, b in data.items()]


def epreuves(spec: dict, brut: dict, autres: dict, frais: float, budget: float) -> dict:
    """Soumet un reglage aux quatre epreuves et rend le detail chiffre.

    Le classement de la page est celui du gain pur, comme demande. Sans ces
    chiffres a cote, il mettrait en tete le reglage qui a le mieux epouse ce
    chemin de prix precis, ce qui est exactement le piege a eviter.
    """
    faire = lambda **kw: Reglages(frais=frais, mise_min=PLANCHER,  # noqa: E731
                                  vente_meme_bougie=False, **{**spec, **kw})
    base = faire()
    paires = _perf(brut, base, budget)

    vois = []
    for cle, facteurs in VOISINAGE.items():
        for f in facteurs:
            try:
                v = faire(**{cle: round(spec.get(cle, getattr(base, cle)) * f, 5)})
            except GrilleError:
                continue
            if v.echelle(1.0, budget)[0][1] >= PLANCHER:
                vois.append(sum(_perf(brut, v, budget)) / len(brut))

    tri = []
    for k in range(4):
        tranche = {s: b[k * len(b) // 4:(k + 1) * len(b) // 4] for s, b in brut.items()}
        tri.append(sum(_perf(tranche, base, budget)) / len(tranche))

    res = {tf: sum(_perf(d, base, budget)) / len(d) for tf, d in autres.items()}
    ecart = max(res.values()) - min(res.values()) if len(res) > 1 else 0.0

    passe = {
        "voisinage": bool(vois) and min(vois) > 0,
        "paires": min(paires) > 0,
        "trimestres": sum(1 for x in tri if x > 0) >= 3,
        "resolutions": ecart < 0.02,
    }
    return {
        "voisin_pire": arrondi(min(vois), 6) if vois else None,
        "paire_pire": arrondi(min(paires), 6),
        "trimestres": [arrondi(x, 6) for x in tri],
        "trimestres_positifs": sum(1 for x in tri if x > 0),
        "resolutions": {k: arrondi(v, 6) for k, v in res.items()},
        "ecart_resolutions": arrondi(ecart, 6),
        "passe": passe,
        "reussies": sum(passe.values()),
    }


def chutes_mesurees() -> dict:
    """La distribution des chutes, si scripts/etudier_chutes.py l'a produite.

    Une chute est un mouvement de baisse continu, du sommet local au creux
    atteint avant retournement, mesure contre la moyenne des 24 heures qui
    precedent son sommet. C'est cette distribution qui dit ou poser les
    barreaux — et c'est elle qui a montre qu'aucune chute unique ne depasse
    -22 %, alors que le plus bas des 400 jours est 50 % sous la moyenne.
    """
    f = Path("docs/chutes.json")
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    tout = [c for v in d["paires"].values() for c in v["chutes"]]
    if not tout:
        return {}
    n = len(tout)
    tranches = [(0.99, 1.01), (0.98, 0.99), (0.97, 0.98), (0.96, 0.97), (0.95, 0.96),
                (0.93, 0.95), (0.90, 0.93), (0.85, 0.90), (0.80, 0.85), (0.75, 0.80),
                (0.70, 0.75), (0.0, 0.70)]
    return {
        "total": n, "rebond": d["rebond"],
        "histogramme": [{"de": a, "a": b, "n": sum(1 for x in tout if a <= x["ratio24"] < b)}
                        for a, b in tranches],
        "cumul": [{"seuil": s, "part": arrondi(
            sum(1 for x in tout if x["ratio24"] <= s) / n, 5)}
            for s in (0.99, 0.98, 0.97, 0.96, 0.95, 0.93, 0.90, 0.85, 0.80, 0.75, 0.70)],
        "pire": arrondi(min(x["ratio24"] for x in tout), 5),
        "duree_mediane_h": arrondi(sorted(x["heures"] + x["reprise_h"] for x in tout)[n // 2], 3),
        "part_sous_12h": arrondi(sum(1 for x in tout if x["heures"] + x["reprise_h"] <= 12) / n, 5),
        "bas_400j": {s: arrondi(v["bas_vs_moyenne"], 5) for s, v in d["paires"].items()},
    }


def exporter(cfg, st: Storage, budget: float) -> dict:
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    par_heure = {"1m": 60, "5m": 12, "15m": 4, "1h": 1}[PAS_SIM]
    paires: dict[str, dict] = {}
    brut: dict[str, list[tuple]] = {}
    t0 = n = None

    for s in cfg.symbols:
        vue = bougies(st, s, PAS_VUE)
        sim = bougies(st, s, PAS_SIM)
        if len(vue) < 100 or len(sim) < 100:
            print(f"  {s} ignoree : {len(vue)} bougies {PAS_VUE}, {len(sim)} en {PAS_SIM}")
            continue
        d0, dn = grille_reguliere(vue, HEURE)
        grille_reguliere(sim, HEURE // par_heure)
        if t0 is None:
            t0, n = d0, dn
        elif (d0, dn) != (t0, n):
            raise SystemExit(f"{s} ne couvre pas la meme fenetre que les autres paires")
        brut[s] = sim
        ouverture, cloture = vue[0][1], vue[-1][4]
        vols = st._conn.execute(
            "SELECT ts, volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
            (s, PAS_VUE)).fetchall()
        vol = {int(r["ts"]): float(r["volume"] or 0.0) for r in vols}
        paires[s] = {
            #  Le prix est le decor commun a toutes les simulations : une seule copie.
            #  Quatre series OHLC plus le volume, au pas horaire. Le navigateur agrege
            #  lui-meme vers 2h, 4h, 12h, 1 jour et 1 semaine : exporter chaque unite
            #  de temps separement multiplierait le poids par six pour aucune
            #  information nouvelle, une bougie longue etant exactement la somme des
            #  courtes qu'elle contient.
            "ouv": [arrondi(x[1], 4) for x in vue],
            "close": [arrondi(x[4], 4) for x in vue],
            "haut": [arrondi(x[2], 4) for x in vue],
            "bas": [arrondi(x[3], 4) for x in vue],
            "vol": [arrondi(vol.get(x[0], 0.0), 3) for x in vue],
            # le repere honnete : acheter au debut, ne rien faire, payer les frais
            "hold_pct": arrondi((cloture / ouverture) * (1 - frais) ** 2 - 1, 5),
        }
    if not paires:
        raise SystemExit("aucun historique exploitable ; lance scripts/fetch_fin.py")

    #  Les autres finesses de bougies, pour l'epreuve de stabilite. Absentes, elle
    #  est simplement sautee plutot que declaree reussie.
    autres = {}
    for tf in ("1h", PAS_SIM, "1m"):
        d = {s: bougies(st, s, tf) for s in cfg.symbols}
        d = {s: b for s, b in d.items() if len(b) > 500}
        if len(d) == len(paires):
            autres[tf] = d
    print(f"  epreuves de stabilite sur : {', '.join(autres) or 'aucune (donnees absentes)'}")

    sorties = []
    semaines = n / 24 / 7
    for spec in REGLAGES:
        cle, nom = spec["cle"], spec["nom"]
        params = {k: v for k, v in spec.items() if k not in ("cle", "nom")}
        faire = lambda m, _p=params: Reglages(  # noqa: E731
            frais=frais, mise_min=m, vente_meme_bougie=False, **_p)
        rg = faire(PLANCHER)
        # l'echelle est la meme a toute epoque, exprimee en fraction de la reference
        ech = rg.echelle(1.0, 1.0)
        bloc = {
            "id": cle, "nom": nom, "profondeur": rg.profondeur, "paliers": rg.paliers,
            "ratio": rg.ratio, "objectif": rg.objectif_net, "abandon_sous": rg.abandon_sous,
            "suivre_hausse": rg.suivre_hausse, "vente_meme_bougie": rg.vente_meme_bougie,
            "depart_sous": rg.depart_sous, "espacement": rg.espacement,
            "courbure": rg.courbure,
            "cliquet_pas": rg.cliquet_pas, "cliquet_retrait": rg.cliquet_retrait,
            "frais_taker": rg.frais_taker, "glissement_stop": rg.glissement_stop,
            "epreuves": epreuves(params, brut, autres, frais, budget),
            "prix_pct": [arrondi(p, 6) for p, _ in ech],
            "mises_pct": [arrondi(e, 8) for _, e in ech],
            "mise_min": PLANCHER,
            "morts": [i for i, (_, e) in enumerate(ech) if e * budget < PLANCHER],
            "sims": {},
        }
        for s, b in brut.items():
            r = rejouer(s, b, rg, budget)
            res = resume(r)
            bloc["sims"][s] = {
                "resume": {k: arrondi(v, 6) if isinstance(v, float) else v
                           for k, v in res.items()},
                "cycles_par_semaine": arrondi(len(r["cycles"]) / semaines, 3),
                # [indice horaire, genre, prix, palier, euros, revient, gain]
                "journal": [[e.ts // HEURE - t0 // HEURE, GENRE[e.genre], arrondi(e.prix, 4),
                             e.palier, arrondi(e.euros, 4), arrondi(e.revient, 4),
                             arrondi(e.gain, 4)] for e in r["journal"]],
                "references": marches(r["suivi"], 1, par_heure),
                "revients": marches(r["suivi"], 2, par_heure),
                "sorties": marches(r["suivi"], 3, par_heure),
                "segments": segments_engages(r["deploiement"], par_heure),
                #  [ouvert, ferme, paliers, investi, gain, gain %, sortie, crans,
                #   heures REELLES, revient, prix de sortie, reference qui a servi]
                #  Les indices d'ouverture et de fermeture sont ramenes a l'heure
                #  pour l'affichage, mais neuf pour cent des cycles se bouclent
                #  DANS une heure : lus seuls, ils annoncent une duree nulle et
                #  font relire a la page la reference d'APRES la vente. Les
                #  quatre derniers champs portent donc ce que le moteur sait.
                "cycles": [[c.ouvert_le // HEURE - t0 // HEURE, c.ferme_le // HEURE - t0 // HEURE,
                            c.paliers, arrondi(c.investi, 2), arrondi(c.gain, 4),
                            arrondi(c.gain_pct, 6), c.sortie, c.crans,
                            arrondi(c.heures, 4), arrondi(c.prix_revient, 4),
                            arrondi(c.prix_sortie, 4), arrondi(c.reference, 4)]
                           for c in r["cycles"]],
                #  PAS "sorties" : cette cle porte deja la serie des prix de
                #  sortie, quelques lignes plus haut. La collision ecrasait
                #  silencieusement la serie, et le trait pointille de la sortie
                #  visee n'etait jamais trace — sur la planche dont c'est le sujet.
                "sorties_type": {g: sum(1 for c in r["cycles"] if c.sortie == g)
                                 for g in ("limite", "stop", "trou")},
                "crans_moyens": arrondi(
                    sum(c.crans for c in r["cycles"]) / len(r["cycles"]), 3) if r["cycles"] else 0.0,
                "jours": journalier(r["deploiement"], r["equity"], budget, 24 * par_heure),
                # ce que le meme reglage aurait donne sous d'autres planchers :
                # sans cette colonne, la page ne pourrait pas montrer combien du
                # resultat tenait a des ordres irrecevables
                "planchers": {
                    f"{m:g}": {
                        "perf_pct": arrondi(resume(rejouer(s, b, faire(m), budget))["perf_pct"], 6),
                        "cycles": len(rejouer(s, b, faire(m), budget)["cycles"]),
                        "vivants": sum(1 for _, e in rg.echelle(1.0, budget) if e >= m),
                    } for m in PLANCHERS_COMPARES
                },
            }
        sorties.append(bloc)

    #  Classes par gain pur, decroissant. L'ordre de la liste est ce que la page
    #  presente en premier : le ranger par cadence mettait en tete un reglage qui
    #  s'agite beaucoup et rapporte peu. On classe donc sur la performance moyenne
    #  des paires, calculee et non decretee, pour que l'ordre suive les donnees.
    def gain_moyen(bloc: dict) -> float:
        v = [s["resume"]["perf_pct"] for s in bloc["sims"].values()]
        return sum(v) / len(v) if v else 0.0

    sorties.sort(key=gain_moyen, reverse=True)
    for rang, bloc in enumerate(sorties, 1):
        bloc["rang"] = rang
        bloc["gain_moyen"] = arrondi(gain_moyen(bloc), 6)

    return {
        "meta": {
            "budget": budget, "frais": frais, "t0": t0, "pas": HEURE, "heures": n,
            "jours": n // 24, "paires": list(paires), "genres": ["achat", "vente", "abandon", "cliquet"],
            "pas_simulation": PAS_SIM, "pas_affichage": PAS_VUE, "plancher": PLANCHER,
            "seuils_epreuves": SEUILS,
        },
        "paires": paires,
        "reglages": sorties,
        "chutes": chutes_mesurees(),
    }


def batir_page(json_texte: str, gabarit: Path, sortie: Path) -> Path:
    """Injecte les donnees dans le gabarit pour obtenir une page autonome.

    Une page qui va chercher son JSON par le reseau ne s'ouvre pas depuis un
    fichier local et ne survit pas a un hebergement qui bloque la requete. Une
    seule page qui se suffit a elle-meme s'ouvre partout.
    """
    #  Sans doctype, un navigateur applique les regles de compatibilite heritees
    #  au lieu du modele de boite standard. L'enveloppe d'un artifact en fournit
    #  un ; un fichier servi tel quel par GitHub Pages, non. On l'ecrit donc ici,
    #  a la construction, et pas dans le gabarit : un doctype egare dans le corps
    #  d'une page deja ouverte est ignore sans dommage.
    modele = "<!doctype html>" + chr(10) + gabarit.read_text(encoding="utf-8")

    jeton = "/*__DONNEES__*/"
    if jeton not in modele:
        raise SystemExit(f"{gabarit} ne contient pas le jeton {jeton}")
    # </script> a l'interieur d'une balise script fermerait la balise : c'est la
    # seule sequence a neutraliser dans du JSON injecte tel quel.
    sortie.write_text(modele.replace(jeton, json_texte.replace("</", "<\\/")), encoding="utf-8")
    return sortie


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--sortie", default="docs/simulations.json")
    ap.add_argument("--page", action="store_true", help="fabrique aussi la page autonome")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    data = exporter(cfg, st, args.budget)
    st.close()

    chemin = Path(args.sortie)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    texte = json.dumps(data, separators=(",", ":"))
    chemin.write_text(texte, encoding="utf-8")
    poids = chemin.stat().st_size / 1e6
    ev = sum(len(sim["journal"]) for r in data["reglages"] for sim in r["sims"].values())
    print(f"{chemin} : {poids:.2f} Mo, {len(data['reglages'])} reglages x "
          f"{len(data['paires'])} paires, {ev} evenements, {data['meta']['jours']} jours")

    if args.page:
        p = batir_page(texte, chemin.parent / "simulations.template.html",
                       chemin.parent / "simulations.html")
        print(f"{p} : {p.stat().st_size / 1e6:.2f} Mo, autonome")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
