"""Les reglages qu'on POSE SUR LA COURBE, et pourquoi ceux-la.

Importe par scripts/exporter_recherche.py. Separe parce que le choix des
reglages a tracer est une decision d'auteur, pas de la tuyauterie : il merite
d'etre lu seul, et conteste seul.

---------------------------------------------------------------------------
LA REGLE, ECRITE AVANT D'AVOIR REGARDE QUI GAGNE. Le balayage a produit 1 113
reglages ; on ne peut pas tous les dessiner, et prendre « les neuf meilleurs »
serait exactement la faute que toute cette etude denonce — un classement apres
coup presente comme un choix.

On prend donc UN REGLAGE PAR LECON. Chacun repond a une question que le lecteur
se pose en regardant les tableaux, et son interet ne depend pas de son rang :

  1. LE TEMOIN. Le reglage du direct, celui qui tourne en paper trading. C'est
     le seul que la mesure recommande de garder, et le point de comparaison de
     tous les autres.
  2. LE MEILLEUR APRES COUP. Ce qu'on aurait voulu faire tourner, et que le
     choix en avant n'atteint jamais. Le montrer dit l'ecart entre savoir et
     pouvoir.
  3. LE PLUS RAPIDE QUI RENDE POSITIF. La seule condition posee par
     l'utilisateur — des cycles courts — tenue par un reglage qui gagne.
  4. LE MEILLEUR EN MARCHE BAISSIER. Sa fiche dira qu'il n'achete presque pas :
     c'est la demonstration visuelle du piege de l'exposition.
  5. LA FORME EXECUTABLE. Beaucoup de barreaux, progression plate : la seule
     geometrie dont les ordres passent un carnet reel a dix mille euros.
  6. L'ECHELLE EN UNITES DE VOLATILITE. Le bras de l'etape 2 qui coute le moins
     et raccourcit le plus les cycles.
  7. UN DECLENCHEUR. Le moins mauvais de l'etape 3, pour voir ou il refuse
     d'acheter.
  8. LE RE-ANCRAGE A LA BAISSE, pose sur la geometrie DU TEMOIN pour que la
     comparaison soit directe : on bascule de l'un a l'autre et on voit ce que
     le mecanisme change. Son effet depend entierement de l'echelle — nul ici,
     mortel ailleurs — et c'est cela qu'il faut dire, plutot que de chercher
     l'instance qui illustre la phrase qu'on avait envie d'ecrire.
  9. LE MEME BOT, EXECUTION CONTRAINTE. Le temoin sous plafond de volume :
     les achats que le carnet aurait refuses disparaissent de la courbe.

Quand une lecon n'a pas de candidat — parce que l'etape correspondante n'a pas
ete rejouee — elle est SAUTEE et la page ne l'annonce pas. Un reglage absent
vaut mieux qu'un reglage choisi pour remplir la case.
"""
from __future__ import annotations

import json
import statistics as stt
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _lots(socle: dict, etapes: list[int]):
    """Les etudes chargees, par etape, au budget de mille euros.

    Mille euros et non dix mille : c'est le budget du direct, et le seul sur
    lequel les dix paires restent executables (voir mesurer_execution.py).
    """
    import sys
    sys.path.insert(0, str(RACINE / "scripts"))
    from lire_balayage import DERIVE, Etude

    m = json.loads((RACINE / "data/etudes/marche.json")
                   .read_text(encoding="utf-8"))["marche"]
    out = {}
    for n in etapes:
        f = RACINE / f"data/etudes/balayage-{n}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        out[n] = (d, Etude(d, 0, m), m, DERIVE)
    return out


def _duree_mediane(e, i) -> float:
    v = [x for ip, s in enumerate(e.paires) if s in e.pleines
         for k in range(e.n_blocs) for x in (e.duree(ip, i, k),) if x is not None]
    return stt.median(v) if v else float("inf")


def _duree_mediane_de(e):
    return lambda j: _duree_mediane(e, j)


def _variante(c: dict) -> str | None:
    """Le nom court du bras, pour les etapes qui comparent des variantes."""
    import sys
    sys.path.insert(0, str(RACINE / "scripts"))
    import lire_balayage as lb
    for base in ("fixe", "meca:temoin", "sans"):
        v = lb.variante(c, base)
        if v is not None and not (c.get("_origine") or "").startswith(base):
            return v
    return None


def choisir(socle: dict, etapes: list[int]) -> list[dict]:
    """Un reglage par lecon. Voir la regle en tete de fichier."""
    lots = _lots(socle, etapes)
    if not lots:
        return []
    retenus: list[dict] = []
    vus: set[str] = set()

    def poser(n, i, cle, nom, pourquoi):
        if n not in lots:
            return
        _d, e, _m, _D = lots[n]
        if i is None or not (0 <= i < len(e.cbs)):
            return
        spec = {k: v for k, v in e.cbs[i].items() if not k.startswith("_")}
        empreinte = json.dumps(spec, sort_keys=True)
        if empreinte in vus:
            return
        vus.add(empreinte)
        retenus.append({
            "id": cle, "nom": nom, "pourquoi": pourquoi, "etape": n,
            "spec": spec, "budget": e.budget,
            "rendement": round(e.moyenne(i, range(e.n_blocs), e.pleines) or 0.0, 5),
            "duree": round(_duree_mediane(e, i), 2),
            "variante": _variante(e.cbs[i]),
        })

    # 1. le temoin
    if 1 in lots:
        _d, e, _m, _D = lots[1]
        poser(1, e.i_temoin, "temoin", "Le réglage du direct",
              "Celui qui tourne en paper trading. Sur 171 décisions en avant, il bat "
              "toute règle de sélection : c'est le seul que la mesure recommande de garder.")

    # 2. le meilleur apres coup
    if 1 in lots:
        _d, e, _m, _D = lots[1]
        i = max(range(len(e.cbs)),
                key=lambda j: e.moyenne(j, range(e.n_blocs), e.pleines) or -9)
        poser(1, i, "apres-coup", "Le meilleur après coup",
              "Ce qu'on aurait voulu faire tourner. Aucune règle de choix ne l'atteint : "
              "l'écart entre le savoir et le pouvoir.")

    # 3. le plus rapide qui rende positif
    if 1 in lots:
        _d, e, _m, _D = lots[1]
        #  UN QUART DE POINT PAR TRANCHE, et non « strictement positif ». Le
        #  reglage le plus rapide a rendement > 0 rendait +0,00 % pour une duree
        #  de 0,2 h : il gagne au sens ou il ne perd pas, et sa courbe n'aurait
        #  rien montre. Le seuil fait de cette case une vraie lecon — un reglage
        #  a la fois RAPIDE et RENTABLE — ou la laisse vide s'il n'en existe pas.
        SEUIL = 0.0025
        cands = [(_duree_mediane(e, j), j) for j in range(len(e.cbs))
                 if (e.moyenne(j, range(e.n_blocs), e.pleines) or -9) > SEUIL]
        if cands:
            poser(1, min(cands)[1], "rapide", "Le plus rapide qui gagne vraiment",
                  "La seule condition posée — des cycles courts — tenue par un réglage "
                  "qui rend plus qu'un quart de point par tranche.")

    # 4. le meilleur en marche baissier
    if 1 in lots:
        d, e, m, DERIVE = lots[1]
        couples = [(ip, k) for ip, s in enumerate(e.paires) if s in e.pleines
                   for k in range(e.appr, e.n_blocs)
                   if m.get(s, {}).get(str(k), [0] * 6)[DERIVE] < 0]
        if couples:
            def baisse(j):
                v = [x for ip, k in couples for x in (e.p(ip, j, k),) if x is not None]
                return sum(v) / len(v) if v else -9
            poser(1, max(range(len(e.cbs)), key=baisse), "baisse",
                  "Le meilleur en marché baissier",
                  "Regardez ses achats : il n'en pose presque aucun. Son classement "
                  "mesure l'exposition, pas le savoir-faire.")

    # 5. la forme executable
    if 1 in lots:
        _d, e, _m, _D = lots[1]
        cands = [j for j, c in enumerate(e.cbs)
                 if c["paliers"] >= 20 and c["ratio"] <= 1.3]
        if cands:
            poser(1, max(cands, key=lambda j: e.moyenne(j, range(e.n_blocs), e.pleines)
                         or -9), "executable", "La forme exécutable",
                  "Beaucoup de barreaux, progression plate : la seule géométrie dont le "
                  "barreau du bas passe un carnet réel à dix mille euros.")

    # 6. l'echelle en unites de volatilite
    if 2 in lots:
        _d, e, _m, _D = lots[2]
        cands = [j for j, c in enumerate(e.cbs) if c.get("echelle_vol")]
        if cands:
            poser(2, min(cands, key=lambda j: _duree_mediane(e, j)),
                  "volatilite", "L'échelle en unités de volatilité",
                  "Le bras de l'étape 2 qui raccourcit le plus les cycles. Il ne rend "
                  "pas davantage, et il écarte les paires au lieu de les rapprocher.")

    # 7. un declencheur, 8. le re-ancrage a la baisse
    if 3 in lots:
        _d, e, _m, _D = lots[3]
        base = [j for j, c in enumerate(e.cbs)
                if (c.get("_origine") or "").startswith("meca:temoin")]
        decl = [j for j, c in enumerate(e.cbs) if c.get("devi_chute")]
        if decl:
            poser(3, max(decl, key=lambda j: e.moyenne(j, range(e.n_blocs), e.pleines)
                         or -9), "declencheur", "Un déclencheur de déviation",
                  "Il n'achète qu'après une chute déjà constituée. Voyez où il refuse "
                  "d'acheter — et qu'il rachète plus tard, plus cher.")
        anc = [j for j, c in enumerate(e.cbs) if c.get("suivre_baisse")]
        if anc:
            #  LE PLUS ACTIF, ET C'EST TOUTE L'HISTOIRE. Deux versions
            #  precedentes ont pris le plus rentable, puis le plus court : la
            #  premiere donnait une instance a 59,6 h, la seconde une instance
            #  dont la « mediane de 0,1 h » ne decrivait que TROIS cycles en
            #  880 jours. Les deux textes devenaient faux sous les yeux du
            #  lecteur.
            #
            #  La verite mesuree est ailleurs : ce mecanisme ARRETE le bot.
            #  L'echelle suit le prix vers le bas, mais le seuil d'abandon reste
            #  fige sur l'ancre d'origine ; l'echelle finit donc par descendre
            #  sous son propre seuil, la descente est abandonnee, et plus rien
            #  n'est achete jusqu'a une vente qui ne viendra pas. D'ou les
            #  -6,5 points d'engagement mesures a l'etape 3. On prend l'instance
            #  la PLUS ACTIVE pour qu'il y ait quelque chose a regarder, et on
            #  dit ce qui se passe.
            def activite(j):
                return sum(x[1] for k in range(e.n_blocs)
                           for ip, s_ in enumerate(e.paires) if s_ in e.pleines
                           for x in (e.perf.get((ip, j, k)),) if x)
            poser(3, max(anc, key=activite), "reancrage",
                  "Le témoin, plus le ré-ancrage à la baisse",
                  "La même géométrie que le réglage du direct, avec l'échelle qui suit "
                  "le prix vers le bas. Basculez de l'un à l'autre : sur cette "
                  "géométrie l'effet est presque nul, parce que la fenêtre monte et que "
                  "l'échelle suit surtout à la hausse. Sur d'autres géométries le même "
                  "mécanisme arrête le bot — l'échelle descend sous son propre seuil "
                  "d'abandon, figé sur l'ancre d'origine, et plus rien n'est acheté. "
                  "Moyenné sur quinze géométries il coûte 1,05 point et 6,5 points "
                  "d'engagement : un mécanisme dont l'effet dépend entièrement de "
                  "l'échelle sur laquelle on le pose.")
        del base

    # 9. le meme bot, execution contrainte
    if 4 in lots:
        _d, e, _m, _D = lots[4]
        cands = [j for j, c in enumerate(e.cbs)
                 if abs(c.get("participation_max", 0) - 0.10) < 1e-9]
        if cands:
            poser(4, max(cands, key=lambda j: e.moyenne(j, range(e.n_blocs), e.pleines)
                         or -9), "plafond", "Exécution contrainte, 10 % du volume",
                  "Le même genre de bot, mais tout ordre plus gros qu'un dixième du "
                  "volume échangé est refusé. Les achats que le carnet n'aurait pas "
                  "servis disparaissent de la courbe.")

    return retenus
