"""Ce que chaque etape mesure. Le banc est dans balayer.py ; ici, le programme.

Separer les deux n'est pas cosmetique : le banc ne doit jamais avoir a savoir ce
qu'il rejoue, et le programme d'une etape doit pouvoir se relire sans traverser
la machinerie de parallelisme. C'est aussi ce qui permet d'ecrire le programme de
l'etape 2 PENDANT que l'etape 1 tourne, sans toucher au fichier que six processus
rechargent a chaque paire.

LES DEUX PLANS SUIVENT LA MEME DISCIPLINE :
  - une CROIX, un axe a la fois autour du temoin, pour lire l'effet de chaque
    levier seul ;
  - des SURFACES completes sur les couples dont on veut voir la forme ;
  - un TIRAGE a graine fixe dans tout l'espace, parce qu'un factoriel reduit ne
    dit rien de ce qui se passe quand deux axes bougent ensemble loin du temoin ;
  - le TEMOIN lui-meme, a la meme place que les autres.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#  Le reglage du direct. Tout ce qu'une etape ne balaye pas garde cette valeur.
TEMOIN = dict(paliers=8, ratio=1.6, profondeur=0.15, depart_sous=0.0,
              objectif_net=0.02, espacement="puissance", courbure=2.0,
              abandon_sous=0.15, suivre_hausse=True, reancrage_min=0.0,
              vente_meme_bougie=False, mise_min=12.0)

GRAINE = 20260914

# ====================================================================== etape 1
#  La geometrie en pourcentage fixe : l'echelle telle que le tableur la posait,
#  la meme sur toutes les monnaies.
AXES_1 = {
    "paliers":      (2, 3, 4, 6, 8, 12, 16, 20, 25, 30),
    "profondeur":   (0.03, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.50),
    "ratio":        (1.0, 1.15, 1.3, 1.5, 1.8, 2.2),
    "objectif_net": (0.005, 0.008, 0.012, 0.02, 0.035, 0.06),
    "forme":        (("lineaire", 1.0), ("geometrique", 1.0),
                     ("puissance", 1.6), ("puissance", 2.5)),
    "depart_sous":  (0.0, 0.003, 0.01, 0.025),
}
SURFACES_1 = (("paliers", "profondeur"), ("paliers", "objectif_net"))
TIRAGES_1 = 330

# ====================================================================== etape 2
#  LA MEME GEOMETRIE, EXPRIMEE EN MULTIPLES DE LA VOLATILITE DE LA PAIRE.
#
#  L'hypothese, et elle est refutable : ainsi exprimees, les dix paires
#  deviennent comparables et un seul reglage vaut pour toutes. La mesure qui
#  tranche n'est PAS le rendement moyen, c'est la DISPERSION entre paires ; elle
#  doit se resserrer. Si elle ne se resserre pas, les classes restent, et on
#  saura sur quelles monnaies la methode s'emploie.
#
#  TROIS DECISIONS, ecrites avant d'avoir vu un seul chiffre de l'etape 1.
#
#  1. LA COMPARAISON EST APPARIEE. Chaque geometrie retenue est rejouee en
#     pourcentage fixe ET en unites de volatilite, la meme geometrie, sur les
#     memes tranches. Comparer le meilleur de l'etape 2 au meilleur de l'etape 1
#     comparerait deux balayages de tailles differentes, ce que la matrice de
#     validation de ce depot a deja montre trompeur.
#
#  2. LA FENETRE DE MESURE EST FIGEE A TROIS JOURS POUR CETTE ETAPE, et balayee
#     separement ensuite sur les seules geometries qui survivent. La balayer ici
#     multiplierait la grille par trois pour un axe dont personne n'attend un
#     effet de premier ordre ; la balayer plus tard coute presque rien.
#
#  3. LES GEOMETRIES SONT CHOISIES PAR UNE REGLE ECRITE D'AVANCE, et cette regle
#     retient QUATRE FAMILLES et non un vainqueur : les dix meilleures au
#     rendement, les dix meilleures parmi celles qui tiennent sous 48 h, les dix
#     meilleures sur les seules tranches baissieres, et la meilleure de chaque
#     nombre de barreaux. La derniere famille est la plus importante : sans elle,
#     l'etape 2 ne visiterait que le voisinage du vainqueur de l'etape 1, et ne
#     pourrait pas dire si la normalisation par la volatilite change la forme
#     meme de l'echelle qu'il faut poser.
AXES_2 = {
    "echelle_vol": ("echelle", "objectif", "tout"),
    #  L'amplitude moyenne a cinq minutes va de 0,012 % (TRX) a 0,149 % (ETH) :
    #  un rapport de 12,4. Les bornes de l'elargissement couvrent un rapport de
    #  25, donc aucune unite de cette liste ne fait buter une paire sur les deux
    #  bords a la fois. La valeur centrale est la moyenne geometrique des deux
    #  extremes du panier, le seul point ou personne n'est etire ni comprime.
    "unite_vol": (0.0003, 0.00042, 0.0006, 0.001),
}
FENETRE_2 = 864          # trois jours de bougies de cinq minutes
BORNES_2 = (0.2, 5.0)    # facteur_min, facteur_max
PAR_FAMILLE = 10


def _spec(base: dict, **kw) -> dict:
    s = dict(base)
    if "forme" in kw:
        s["espacement"], s["courbure"] = kw.pop("forme")
    s.update(kw)
    return s


def _valideur(frais: float):
    from src.grille import GrilleError, Reglages

    def ok(spec: dict) -> bool:
        try:
            Reglages(frais=frais, **{k: v for k, v in spec.items()
                                     if not k.startswith("_")})
        except GrilleError:
            return False
        return True
    return ok


def _recolte():
    """Un sac a combinaisons qui dedoublonne et garde l'origine de la premiere."""
    vus: dict[str, dict] = {}

    def poser(spec: dict, origine: str, valide) -> None:
        if not valide(spec):
            return
        cle = json.dumps({k: v for k, v in spec.items() if not k.startswith("_")},
                         sort_keys=True)
        vus.setdefault(cle, dict(spec, _origine=origine))
    return vus, poser


def plan_etape1(rapide: bool, frais: float) -> list[dict]:
    """La croix, deux surfaces, puis un tirage dans tout l'espace restant."""
    axes = {k: (v[::3] if rapide and len(v) > 3 else v) for k, v in AXES_1.items()}
    valide = _valideur(frais)
    vus, poser = _recolte()
    poser(dict(TEMOIN), "temoin", valide)
    for nom, vals in axes.items():
        for v in vals:
            poser(_spec(TEMOIN, **{nom: v}), f"croix:{nom}", valide)
    for a, b in SURFACES_1:
        for va in axes[a]:
            for vb in axes[b]:
                poser(_spec(TEMOIN, **{a: va, b: vb}), f"surface:{a}x{b}", valide)
    rng = random.Random(GRAINE)
    vise = 40 if rapide else TIRAGES_1
    tires = essais = 0
    while tires < vise and essais < vise * 40:
        essais += 1
        avant = len(vus)
        poser(_spec(TEMOIN, **{k: rng.choice(v) for k, v in axes.items()}),
              "tirage", valide)
        tires += len(vus) > avant
    return list(vus.values())


# ---------------------------------------------------------------- etape 2
def geometries_retenues(chemin: str = "data/etudes/balayage-1.json",
                        par_famille: int = PAR_FAMILLE) -> list[dict]:
    """Les geometries que l'etape 2 reprend, par la regle de la decision 3.

    Les rendements lus ici sont ceux de TOUTES les tranches : c'est un choix
    APRES COUP, et il est assume. L'etape 2 ne pretend pas valider ces
    geometries — elle demande si les exprimer en unites de volatilite resserre
    la dispersion entre paires. La question est appariee, donc immunisee au
    fait que le point de depart soit choisi apres coup : les deux bras partent
    du meme point.
    """
    d = json.loads((RACINE / chemin).read_text(encoding="utf-8"))
    cbs = d["combinaisons"]
    pleines = {s for s in d["paires"] if s not in set(d["partielles"])}
    ip = {s: i for i, s in enumerate(d["paires"])}
    #  On lit le budget de mille euros : c'est celui du direct, et le seul sur
    #  lequel les dix paires restent executables (voir mesurer_execution.py).
    perf: dict[tuple[int, int], list] = {}
    for x in d["matrice"]:
        if x[2] == 0 and d["paires"][x[0]] in pleines:
            perf.setdefault((x[1], x[3]), []).append(x)

    baissieres = set()
    m = json.loads((RACINE / "data/etudes/marche.json").read_text(encoding="utf-8"))
    for s, bl in m["marche"].items():
        if s in pleines:
            for k, v in bl.items():
                if v[4] < 0:
                    baissieres.add((ip[s], int(k)))

    def note(i, filtre=None):
        v = [x[4] for k in range(d["n_blocs"]) for x in perf.get((i, k), [])
             if filtre is None or (x[0], x[3]) in filtre]
        return sum(v) / len(v) if v else None

    def duree_med(i):
        v = sorted(x[11] for k in range(d["n_blocs"]) for x in perf.get((i, k), [])
                   if x[11] >= 0)
        return v[len(v) // 2] if v else float("inf")

    notes = {i: note(i) for i in range(len(cbs))}
    ok = [i for i in notes if notes[i] is not None]
    familles = {
        "rendement": sorted(ok, key=lambda i: -notes[i])[:par_famille],
        "rapides": sorted([i for i in ok if duree_med(i) < 48],
                          key=lambda i: -notes[i])[:par_famille],
        "baisse": sorted(ok, key=lambda i: -(note(i, baissieres) or -9))[:par_famille],
    }
    par_paliers = {}
    for i in ok:
        p = cbs[i]["paliers"]
        if p not in par_paliers or notes[i] > notes[par_paliers[p]]:
            par_paliers[p] = i
    familles["paliers"] = sorted(par_paliers.values())

    vus: dict[str, dict] = {}
    #  LE TEMOIN EST TOUJOURS DU LOT, ET IL PASSE EN PREMIER. Les quatre familles
    #  ci-dessus choisissent d'apres le rendement APRES COUP ; or l'etape 1 a
    #  mesure que le temoin — le reglage du direct, jamais rechoisi — bat toute
    #  regle de selection en avant (+0,21 % par tranche contre -3,32 % pour le
    #  choix global). Il se classe donc mal apres coup et serait absent du lot,
    #  alors que c'est precisement LUI qu'il faut chercher a ameliorer : la seule
    #  question qui reste apres l'etape 1 est « un mecanisme rend-il meilleur ce
    #  qu'on fait deja tourner », et on ne peut y repondre sans lui.
    t = dict(TEMOIN)
    vus[json.dumps(t, sort_keys=True)] = dict(t, _famille="temoin")
    for fam, idx in familles.items():
        for i in idx:
            g = {k: v for k, v in cbs[i].items() if not k.startswith("_")}
            cle = json.dumps(g, sort_keys=True)
            if cle not in vus:
                vus[cle] = dict(g, _famille=fam)
    return list(vus.values())


def plan_etape2(rapide: bool, frais: float) -> list[dict]:
    """Chaque geometrie retenue, en pourcentage fixe puis en unites de volatilite."""
    geos = geometries_retenues()
    if rapide:
        geos = geos[:4]
    modes = AXES_2["echelle_vol"][:2] if rapide else AXES_2["echelle_vol"]
    unites = AXES_2["unite_vol"][::2] if rapide else AXES_2["unite_vol"]
    valide = _valideur(frais)
    vus, poser = _recolte()
    for g in geos:
        fam = g.get("_famille", "?")
        #  LE BRAS TEMOIN DE L'ETAPE 2 : la meme geometrie, sans normalisation.
        #  Il est rejoue ICI plutot que relu dans le fichier de l'etape 1, pour
        #  que les deux bras traversent exactement le meme code au meme moment.
        poser({k: v for k, v in g.items() if not k.startswith("_")},
              f"fixe:{fam}", valide)
        for mode in modes:
            for u in unites:
                poser(dict({k: v for k, v in g.items() if not k.startswith("_")},
                           echelle_vol=mode, unite_vol=u, fenetre_vol=FENETRE_2,
                           facteur_min=BORNES_2[0], facteur_max=BORNES_2[1]),
                      f"vol:{mode}:{fam}", valide)
    return list(vus.values())


# ====================================================================== etape 3
#  LES MECANISMES : non plus OU l'echelle achete, mais QUAND elle en a le droit.
#
#  TROIS DECISIONS, ecrites avant d'avoir vu un chiffre de l'etape 2.
#
#  1. UN MECANISME A LA FOIS. Trois declencheurs a trois valeurs chacun feraient
#     vingt-sept combinaisons par geometrie, et un vainqueur a trois conditions
#     dont on ignorerait laquelle travaille. Teste separement, chaque mecanisme
#     rend un chiffre qu'on peut attribuer. Les paires de mecanismes sont
#     l'etape 3b, et elles ne portent que sur ce qui a paye seul.
#
#  2. LES DEUX SENS DU FILTRE DE MOYENNE SONT BALAYES. « Acheter sous la
#     moyenne » est l'intuition de la grille — on achete les creux. « Acheter
#     au-dessus » est l'intuition inverse — on n'achete qu'en tendance. Ne
#     mesurer que le premier serait mesurer une croyance.
#
#  3. LES DEUX ECHAPPATOIRES SONT DANS LA MEME LISTE que les declencheurs, et
#     non a part : le re-ancrage a la baisse et la sortie a cent jours changent
#     le rendement comme les autres, et les comparer dans le meme tableau est le
#     seul moyen de dire si le probleme du capital bloque se resout mieux en
#     n'achetant pas qu'en vendant tard.
MECANISMES_3 = [
    ("temoin", {}),
    #  (b) la deviation : n'acheter qu'apres une chute deja constituee
    ("b:1 % sur 6 h", dict(devi_chute=0.01, devi_fenetre=72)),
    ("b:2 % sur 6 h", dict(devi_chute=0.02, devi_fenetre=72)),
    ("b:2 % sur 24 h", dict(devi_chute=0.02, devi_fenetre=288)),
    ("b:5 % sur 24 h", dict(devi_chute=0.05, devi_fenetre=288)),
    #  (c) le filtre de moyenne, dans les deux sens (decision 2)
    ("c:sous 1 j", dict(filtre_moyenne=288, filtre_sens="sous")),
    ("c:dessus 1 j", dict(filtre_moyenne=288, filtre_sens="dessus")),
    ("c:sous 7 j", dict(filtre_moyenne=2016, filtre_sens="sous")),
    ("c:dessus 7 j", dict(filtre_moyenne=2016, filtre_sens="dessus")),
    #  (e) la confirmation de rebond
    ("e:1 hausse", dict(confirmation=1)),
    ("e:2 hausses", dict(confirmation=2)),
    ("e:3 hausses", dict(confirmation=3)),
    #  les deux echappatoires (decision 3)
    ("re-ancrage baisse", dict(suivre_baisse=True, reancrage_min=0.002)),
    ("sortie 100 jours", dict(sortie_jours=100)),
]
PAR_FAMILLE_3 = 5


def plan_etape3(rapide: bool, frais: float) -> list[dict]:
    """Chaque geometrie survivante, avec un mecanisme a la fois."""
    src = ("data/etudes/balayage-2.json"
           if (RACINE / "data/etudes/balayage-2.json").exists()
           else "data/etudes/balayage-1.json")
    geos = geometries_retenues(src, PAR_FAMILLE_3)
    if rapide:
        geos = geos[:3]
    mecas = MECANISMES_3[:4] if rapide else MECANISMES_3
    valide = _valideur(frais)
    vus, poser = _recolte()
    for g in geos:
        fam = g.get("_famille", "?")
        base = {k: v for k, v in g.items() if not k.startswith("_")}
        for nom, kw in mecas:
            #  reancrage_min appartient a la geometrie ET au re-ancrage a la
            #  baisse : ecraser sans le dire changerait deux choses a la fois.
            poser(dict(base, **kw), f"meca:{nom}:{fam}", valide)
    return list(vus.values())


# ====================================================================== etape 4
#  L'ENCADREMENT : le meme reglage, avec et sans plafond de volume.
#
#  Les etapes 1 a 3 rejouent un moteur qui sert un ordre ENTIER, INSTANTANEMENT,
#  sans jamais regarder s'il s'est echange quoi que ce soit. Mesure faite sur les
#  880 jours : sur six paires du panier, le barreau du bas d'une echelle a dix
#  mille euros n'aurait pu etre absorbe que dans 0,02 a 0,08 % des bougies de
#  cinq minutes. Les rendements de ces paires sont donc des MAJORANTS.
#
#  Le plafond refuse en entier un ordre trop gros pour la bougie. C'est trop
#  severe — dans la realite il serait servi en partie — donc il MINORE. Les deux
#  ensemble encadrent, et un encadrement vaut mieux qu'une correction dont
#  personne ne saurait dire le sens de l'erreur.
#
#  TROIS PARTICIPATIONS plutot qu'une : a 5 %, 10 % et 20 % du volume, on voit
#  si la conclusion depend du chiffre choisi. Si les trois disent la meme chose,
#  l'hypothese n'a pas d'importance ; si elles divergent, c'est elle qui decide,
#  et il faudra le dire plutot que de choisir la plus flatteuse.
PARTICIPATIONS_4 = (0.05, 0.10, 0.20)
PAR_FAMILLE_4 = 4


def plan_etape4(rapide: bool, frais: float) -> list[dict]:
    """Les reglages survivants, sans plafond puis sous trois plafonds."""
    src = next((c for c in ("data/etudes/balayage-3.json",
                            "data/etudes/balayage-2.json",
                            "data/etudes/balayage-1.json")
                if (RACINE / c).exists()), None)
    if src is None:
        raise SystemExit("aucun balayage a encadrer")
    geos = geometries_retenues(src, PAR_FAMILLE_4)
    if rapide:
        geos = geos[:2]
    parts = PARTICIPATIONS_4[:1] if rapide else PARTICIPATIONS_4
    valide = _valideur(frais)
    vus, poser = _recolte()
    for g in geos:
        fam = g.get("_famille", "?")
        base = {k: v for k, v in g.items() if not k.startswith("_")}
        poser(base, f"sans:{fam}", valide)
        for p in parts:
            poser(dict(base, participation_max=p), f"plafond:{p:.0%}:{fam}", valide)
    return list(vus.values())


PLANS = {1: plan_etape1, 2: plan_etape2, 3: plan_etape3, 4: plan_etape4}

#  Les etapes qui ont besoin du VOLUME dans les bougies. Le banc charge cinq
#  champs par defaut ; en charger six partout couterait 20 % de memoire pour
#  rien, puisque seul le plafond de participation le lit.
BESOIN_VOLUME = {4}
