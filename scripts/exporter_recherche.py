"""Prepare ce que la page d'etude affiche, et rien d'autre.

    python scripts/exporter_recherche.py
    python scripts/exporter_recherche.py --etapes 1,2

La page ne calcule aucun verdict : elle affiche. Toutes les regles de decision
sont dans scripts/lire_balayage.py, et ce script applique EXACTEMENT les memes,
en les important plutot qu'en les recopiant. Une regle recopiee dans deux
fichiers finit toujours par diverger, et c'est alors la page publiee qui ment.

CE QUI EST EXPORTE, ET CE QUI NE L'EST PAS. La matrice brute pese des dizaines
de megaoctets et personne ne telecharge cela pour lire un tableau. On exporte :
  - le marche, paire par paire et tranche par tranche (leger) ;
  - l'executabilite, paire par paire (leger) ;
  - les deux surfaces completes, agregees sur les paires (quelques milliers de
    nombres) ;
  - le nuage duree-rendement, un point par combinaison ;
  - le verdict du choix en avant, quelques dizaines de nombres ;
  - les fiches, une par paire ;
  - les bougies d'affichage et les ordres d'UN reglage retenu par paire, pour
    que le graphique partage ait de quoi montrer.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stt
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from lire_balayage import (  # noqa: E402
    DERIVE, MED1H, MOY5, PLAT1H, PLAT5, Etude, corr_rang, resume_spec)

MS_JOUR = 86_400_000
PAS_VUE = 3_600_000          # les bougies d'affichage sont horaires


def arrondir(x, n=5):
    return None if x is None else round(x, n)


def bloc_marche(m: dict, paires: list[str], partielles: set[str]) -> dict:
    out = {}
    for s in paires:
        bl = m.get(s, {})
        if not bl:
            continue
        v = list(bl.values())
        n = len(v)
        out[s] = {
            "tranches": n,
            "partielle": s in partielles,
            "moy5": round(sum(x[MOY5] for x in v) / n, 8),
            "med1h": round(sum(x[MED1H] for x in v) / n, 8),
            "plat5": round(sum(x[PLAT5] for x in v) / n, 4),
            "plat1h": round(sum(x[PLAT1H] for x in v) / n, 4),
            "derive": round(sum(x[DERIVE] for x in v) / n, 5),
            "baissieres": sum(1 for x in v if x[DERIVE] < 0),
            "par_tranche": {k: [x[MOY5], x[MED1H], x[PLAT5], x[DERIVE]]
                            for k, x in bl.items()},
        }
    return out


def bloc_stabilite(m: dict, paires: list[str]) -> dict:
    """La mesure qui autorise, ou non, a classer les paires d'avance."""
    out = {}
    for crit, nom in ((MOY5, "moy5"), (MED1H, "med1h")):
        ks = sorted({int(k) for s in paires for k in m.get(s, {})})
        cs = []
        for a, b in zip(ks, ks[1:]):
            com = [s for s in paires if str(a) in m.get(s, {}) and str(b) in m.get(s, {})]
            if len(com) >= 6:
                cs.append(corr_rang([m[s][str(a)][crit] for s in com],
                                    [m[s][str(b)][crit] for s in com]))
        if cs:
            out[nom] = {"moyenne": round(sum(cs) / len(cs), 3),
                        "min": round(min(cs), 3), "max": round(max(cs), 3),
                        "n": len(cs)}
    return out


def bloc_surfaces(e: Etude, d: dict) -> dict:
    """Les deux surfaces completes : une case par couple de valeurs d'axes.

    Agregees sur les paires PLEINES et sur toutes les tranches. C'est
    DESCRIPTIF : la surface dit la forme du terrain, pas ce qu'il faut choisir.

    LES CASES SONT RETROUVEES PAR VALEUR, JAMAIS PAR ORIGINE. Le plan pose la
    croix avant les surfaces et dedoublonne : une combinaison qui appartient aux
    deux garde l'etiquette « croix », et un filtre sur l'etiquette perdait donc
    toute la ligne du temoin — 8 cases sur 80 pour la premiere surface, 16 sur
    60 pour la seconde, et precisement les plus interessantes. On construit la
    specification attendue et on la cherche dans l'index : elle est la, quelle
    que soit la raison pour laquelle elle a ete posee.
    """
    #  Seule l'etape 1 balaye une grille de valeurs : les suivantes comparent des
    #  variantes appariees, ou une « surface » n'aurait aucun sens.
    axes = d.get("axes") or {}
    if not axes:
        return {}
    index = {json.dumps({k: v for k, v in c.items() if not k.startswith("_")},
                        sort_keys=True): i for i, c in enumerate(e.cbs)}
    temoin = d["temoin"]
    out = {}
    for a, b in (("paliers", "profondeur"), ("paliers", "objectif_net")):
        cases: dict[str, list] = {}
        manquantes = 0
        for va in axes[a]:
            for vb in axes[b]:
                spec = dict(temoin)
                spec[a], spec[b] = va, vb
                i = index.get(json.dumps(spec, sort_keys=True))
                if i is None:
                    manquantes += 1
                    continue
                m = e.moyenne(i, range(e.n_blocs), e.pleines)
                if m is None:
                    continue
                ds = sorted(x for ip, s in enumerate(e.paires) if s in e.pleines
                            for k in range(e.n_blocs)
                            for x in (e.duree(ip, i, k),) if x is not None)
                cases[f"{va}|{vb}"] = [round(m, 5),
                                       round(stt.median(ds), 1) if ds else None, i]
        if manquantes:
            print(f"    surface {a}x{b} : {manquantes} cases absentes du balayage")
        out[f"{a}x{b}"] = {"axes": [a, b], "cases": cases,
                           "valeurs": {a: list(axes[a]), b: list(axes[b])}}
    return out


def bloc_nuage(e: Etude) -> list:
    """Un point par combinaison : duree mediane, rendement, forme de l'echelle.

    C'est la figure qui repond a la seule condition posee — des cycles sous
    quarante-huit heures — parce qu'elle montre d'un coup d'oeil s'il existe
    des combinaisons a la fois rapides et rentables, ou si les deux s'excluent.
    """
    out = []
    for i, c in enumerate(e.cbs):
        m = e.moyenne(i, range(e.n_blocs), e.pleines)
        if m is None:
            continue
        ds = sorted(x for ip, s in enumerate(e.paires) if s in e.pleines
                    for k in range(e.n_blocs)
                    for x in (e.duree(ip, i, k),) if x is not None)
        if not ds:
            continue
        #  stt.median ET NON sorted[n//2] : sur un effectif PAIR — 9 paires fois
        #  22 tranches en font 198 — le premier moyenne les deux valeurs centrales
        #  quand le second prend la superieure. Une combinaison passait ainsi de
        #  47,21 h a 48,0 h selon la fonction, et le compte rendu annoncait 397
        #  combinaisons sous 48 h la ou le lecteur en comptait 398. Deux endroits
        #  qui disent « la mediane » doivent calculer la meme chose.
        out.append([i, round(stt.median(ds), 2), round(m, 5), c["paliers"],
                    c["ratio"], c["objectif_net"], c["profondeur"],
                    c.get("_origine", "")])
    return out


def bloc_fiches(e: Etude, retenus: list, marche: dict) -> dict:
    cl0 = e.classes(e.n_blocs, MOY5)
    cl1 = e.classes(e.n_blocs, MED1H)
    out = {}
    for ip, s in enumerate(e.paires):
        if not marche.get(s):
            continue
        av = [x for k, i in retenus for x in (e.p(ip, i, k),) if x is not None]
        tm = ([x for k in range(e.appr, e.n_blocs)
               for x in (e.p(ip, e.i_temoin, k),) if x is not None]
              if e.i_temoin is not None else [])
        best = max(range(len(e.cbs)),
                   key=lambda i: e.moyenne(i, range(e.n_blocs), [s]) or -9.0)
        out[s] = {
            "classe_moy5": cl0.get(s), "classe_med1h": cl1.get(s),
            "avant": arrondir(sum(av) / len(av) if av else None),
            "temoin": arrondir(sum(tm) / len(tm) if tm else None),
            "meilleur_apres_coup": arrondir(e.moyenne(best, range(e.n_blocs), [s])),
            "meilleur_spec": resume_spec(e.cbs[best]),
            "meilleur_i": best,
        }
    return out


def bloc_avant(e: Etude) -> dict:
    """Le verdict, calcule par la MEME fonction que le compte rendu texte.

    La fonction est appelee, pas recopiee, et elle rend des NOMBRES. La page ne
    peut donc pas afficher un verdict different de celui qu'imprime le script,
    meme si la regle de choix change un jour. Son impression est avalee : elle
    n'a rien a faire dans la sortie d'un exportateur.
    """
    import contextlib
    import io
    import lire_balayage as lb
    from accentuer import accentuer_prose
    with contextlib.redirect_stdout(io.StringIO()):
        retenus, reperes = lb.section_avant(e)
    #  LES ACCENTS SONT POSES ICI, PAS DANS LE SCRIPT. Les noms des reperes sont
    #  d'abord imprimes sur une console Windows en cp1252, ou un accent ressort
    #  en point d'interrogation ; ils finissent ensuite sur une page web en UTF-8,
    #  ou « temoin » et « mediane » sont des fautes. Meme separation que pour le
    #  journal de bord : le texte vit sans accents la ou il transite, et les
    #  recoit la ou il se lit.
    return {"retenus": retenus,
            "reperes": [{k: (round(v, 6) if isinstance(v, float) else
                             accentuer_prose(v) if k == "nom" else v)
                         for k, v in r.items()} for r in reperes],
            "n_decisions": len(retenus) * len(e.pleines)}


def bloc_appariee(e: Etude, etape: int) -> dict:
    """La comparaison appariee des etapes 2 et 3 : la meme geometrie, avec et sans.

    C'est la seule mesure de ce banc qui echappe au piege du choix : elle ne
    classe rien, elle compare chaque bras a LUI-MEME sur les memes tranches.
    """
    import contextlib
    import io
    import lire_balayage as lb
    cfg = {2: ("fixe", "l'echelle en unites de volatilite"),
           3: ("meca:temoin", "les mecanismes, un a la fois"),
           4: ("sans", "l'encadrement par le volume echange")}
    if etape not in cfg:
        return {}
    base, titre = cfg[etape]
    with contextlib.redirect_stdout(io.StringIO()):
        out = lb.section_appariee(e, titre, base)
    out["titre"] = titre
    return out


def bloc_baisse(e: Etude) -> dict:
    """Ce que les marches en baisse disent — la question meme de la demande.

    La fonction du lecteur est appelee, pas recopiee : la page affiche donc les
    memes nombres que le compte rendu texte, y compris le test d'exposition qui
    vide de son sens le chiffre de stabilite.
    """
    import contextlib
    import io
    import lire_balayage as lb
    with contextlib.redirect_stdout(io.StringIO()):
        return lb.section_baisse(e)


def bloc_ordres(cx, sym: str, spec: dict, budget: float, frais: float,
                t0: int, n_blocs: int, bloc_j: int, pas_vue: int) -> dict:
    """Rejoue UN reglage sur toute la fenetre et rend de quoi le poser sur la courbe.

    C'est ce qui separe une page de resultats d'une page d'ETUDE : un tableau dit
    qu'un reglage a rendu tant, le graphique dit OU il a achete, combien de fois
    il est reste bloque, et a quoi ressemblait le marche quand il s'est trompe.

    LE REJEU SE FAIT A CINQ MINUTES, comme la mesure — jamais a l'heure. Rejouer
    a l'heure pour « aller plus vite » donnerait d'autres achats que ceux qui ont
    ete mesures, et la courbe illustrerait un robot qui n'a jamais tourne. Seules
    les COURBES continues sont ensuite ramenees au pas d'affichage : un point par
    heure suffit a dessiner un prix de revient, et 253 440 points ne se
    telechargent pas.
    """
    from src.grille import Reglages, rejouer     # importe ici : inutile au socle

    fin = t0 + n_blocs * bloc_j * MS_JOUR
    b = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
         for r in cx.execute(
             "SELECT ts,open,high,low,close FROM candles WHERE symbol=? "
             "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts", (sym, t0, fin))]
    if not b:
        return {}
    rg = Reglages(frais=frais, **{k: v for k, v in spec.items()
                                  if not k.startswith("_")})
    r = rejouer(sym, b, rg, budget, trace=True)
    ordres = [{"ts": e.ts, "prix": round(e.prix, 8), "euros": round(e.euros, 2),
               "genre": e.genre}
              for e in r["journal"] if e.genre in ("achat", "vente")]
    cycles = [{"t0": c.ouvert_le, "t1": c.ferme_le, "gain": round(c.gain_pct, 5),
               "paliers": c.paliers, "heures": round(c.heures, 1)}
              for c in r["cycles"]]
    #  Les bandes : les heures ou du capital dort. On les deduit du deploiement
    #  plutot que des cycles, parce qu'un cycle abandonne n'a pas de fin.
    bandes, debut = [], None
    for ts, _cash, engage, _n, _ab in r["deploiement"]:
        if engage > 1e-9 and debut is None:
            debut = ts
        elif engage <= 1e-9 and debut is not None:
            bandes.append([debut, ts])
            debut = None
    if debut is not None:
        bandes.append([debut, r["deploiement"][-1][0]])
    saut = max(1, pas_vue // 300_000)

    def courbe(col: int) -> list:
        """Une courbe reduite a ses CHANGEMENTS, et non a un point par heure.

        Le prix de revient ne bouge qu'aux achats : entre deux, des centaines de
        points portent la meme valeur et pesent sans rien montrer. On garde le
        point ou la valeur change ET CELUI QUI LE PRECEDE, ce qui conserve la
        marche d'escalier ; sans ce second point, le trace joindrait en pente
        douce deux paliers qui ont saute d'un coup, et le graphique mentirait
        sur la vitesse a laquelle le prix de revient descend — c'est-a-dire sur
        le mecanisme meme de la strategie.
        """
        pts, prec_ts, prec_v = [], None, None
        for i, ligne in enumerate(r["suivi"]):
            if i % saut:
                continue
            ts_, v = ligne[0], ligne[col]
            v = round(v, 8) if v > 0 else 0.0
            if v != prec_v:
                if prec_v is not None and prec_ts is not None and prec_v > 0:
                    pts.append([prec_ts, prec_v])
                if v > 0:
                    pts.append([ts_, v])
            prec_ts, prec_v = ts_, v
        return pts

    revient, sortie = courbe(2), courbe(3)
    return {"ordres": ordres, "cycles": cycles, "bandes": bandes,
            "revient": revient, "sortie": sortie,
            "spec": {k: v for k, v in spec.items() if not k.startswith("_")},
            "budget": budget}


def bloc_graphique(cx, sym: str, t0: int, n_blocs: int, bloc_j: int) -> dict:
    """Les bougies d'AFFICHAGE d'une paire : horaires, sur toute la fenetre."""
    fin = t0 + n_blocs * bloc_j * MS_JOUR
    r = [(int(a), float(b), float(c), float(d), float(f), float(g))
         for a, b, c, d, f, g in cx.execute(
             "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? "
             "AND timeframe='1h' AND ts>=? AND ts<? ORDER BY ts", (sym, t0, fin))]
    return {"pas": PAS_VUE,
            "bougies": [[x[0], round(x[1], 8), round(x[2], 8), round(x[3], 8),
                         round(x[4], 8), round(x[5], 4)] for x in r]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--etapes", default="1")
    ap.add_argument("--sortie", default="docs/data")
    ap.add_argument("--sans-vues", action="store_true",
                    help="ne pas refaire les fichiers de graphique : ils demandent "
                         "un rejeu trace de cinq minutes et ne changent pas quand "
                         "seul le verdict a bouge")
    args = ap.parse_args()

    dossier = RACINE / args.sortie
    dossier.mkdir(parents=True, exist_ok=True)

    mf = RACINE / "data/etudes/marche.json"
    ex = RACINE / "data/etudes/execution.json"
    if not mf.exists():
        raise SystemExit("data/etudes/marche.json absent ; lance mesurer_marche.py")
    m = json.loads(mf.read_text(encoding="utf-8"))

    socle = {
        "t0": m["t0"], "n_blocs": m["n_blocs"], "bloc_jours": m["bloc_jours"],
        "criteres": m["criteres"],
        "stabilite": bloc_stabilite(m["marche"], list(m["marche"])),
    }
    if ex.exists():
        e = json.loads(ex.read_text(encoding="utf-8"))
        socle["execution"] = {
            "participation": e["participation"], "paliers": e["paliers"],
            "ratio": e["ratio"], "mises": e["mises"],
            "paires": {s: {k: v for k, v in d.items() if k != "bougies"}
                       for s, d in e["paires"].items()},
        }

    #  Le socle vit sans aucune etape : le marche et l'executabilite ne
    #  dependent d'aucun rejeu, et la page doit pouvoir les montrer seuls.
    socle["paires"] = [s for s in m["marche"] if m["marche"][s]]
    socle["partielles"] = [s for s in socle["paires"]
                           if len(m["marche"][s]) < m["n_blocs"]]
    socle["marche"] = bloc_marche(m["marche"], socle["paires"],
                                  set(socle["partielles"]))

    etapes = [int(x) for x in args.etapes.split(",") if x.strip()]
    for n in etapes:
        f = RACINE / f"data/etudes/balayage-{n}.json"
        if not f.exists():
            print(f"  etape {n} : {f.name} absent, ignoree")
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if d["t0"] != m["t0"]:
            raise SystemExit(f"etape {n} : fenetre differente de celle du marche")
        partielles = set(d["partielles"])
        socle["paires"] = d["paires"]
        socle["partielles"] = sorted(partielles)
        socle["marche"] = bloc_marche(m["marche"], d["paires"], partielles)
        socle["frais"] = d["frais"]
        bloc = {"budgets": d["budgets"], "temoin": d["temoin"],
                "combinaisons": d["combinaisons"], "vivants": d["vivants"],
                "budget": {}}
        for ib, b in enumerate(d["budgets"]):
            et = Etude(d, ib, m["marche"])
            av = bloc_avant(et)
            bloc["budget"][str(int(b))] = {
                "surfaces": bloc_surfaces(et, d),
                "nuage": bloc_nuage(et),
                "avant": av,
                "appariee": bloc_appariee(et, n),
                "baisse": bloc_baisse(et),
                "fiches": bloc_fiches(et, av["retenus"], m["marche"]),
                "i_temoin": et.i_temoin,
            }
            print(f"  etape {n}, budget {b:.0f} : {len(bloc['budget'][str(int(b))]['nuage'])} "
                  f"points, {av['n_decisions']} decisions en avant")
        socle[f"etape{n}"] = bloc

    chemin = dossier / "etude.json"
    chemin.write_text(json.dumps(socle, separators=(",", ":")), encoding="utf-8")
    print(f"  ecrit dans {chemin.relative_to(RACINE)} "
          f"({chemin.stat().st_size / 1e6:.2f} Mo)")

    #  Les bougies d'affichage, un fichier par paire : la page ne les charge
    #  qu'au clic, comme le simulateur. Sans ce decoupage, la page pese huit
    #  megaoctets et personne ne l'ouvre.
    cx = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    vues = dossier / "etude-vue"
    vues.mkdir(exist_ok=True)
    #  LE REGLAGE POSE SUR LA COURBE EST LE TEMOIN — le reglage du direct, celui
    #  qui tourne en paper trading. Ce n'est pas un choix par defaut : c'est la
    #  conclusion de l'etape 1. Sur 171 decisions en avant, il rend +0,21 % par
    #  tranche quand le choix global rend -3,32 % et le tirage au sort -2,15 %.
    #  Montrer le « meilleur apres coup » illustrerait un robot qui n'a jamais
    #  tourne ; montrer le choix en avant illustrerait une regle que la mesure
    #  vient de rejeter. On montre celui qu'il faut garder.
    pose = None
    for n in sorted(etapes):
        b = socle.get(f"etape{n}")
        if not b:
            continue
        cle = sorted(b["budget"], key=float)[0]
        i = b["budget"][cle].get("i_temoin")
        if i is not None:
            pose = (b["combinaisons"][i], float(cle), n)
            break
    if pose:
        print(f"  reglage pose sur la courbe — le TEMOIN (budget {pose[1]:.0f}) : "
              f"{resume_spec(pose[0])}")

    for s in ([] if args.sans_vues else socle.get("paires", [])):
        g = bloc_graphique(cx, s, m["t0"], m["n_blocs"], m["bloc_jours"])
        if not g["bougies"]:
            print(f"    {s} : aucune bougie horaire, graphique indisponible")
            continue
        if pose:
            g.update(bloc_ordres(cx, s, pose[0], pose[1], socle.get("frais", 0.001),
                                 m["t0"], m["n_blocs"], m["bloc_jours"], PAS_VUE))
        p = vues / (s.replace("/", "-") + ".json")
        p.write_text(json.dumps(g, separators=(",", ":")), encoding="utf-8")
        print(f"    {s:<10} {len(g['bougies']):>6} bougies, "
              f"{len(g.get('ordres', [])):>5} ordres, {len(g.get('cycles', [])):>4} cycles "
              f"({p.stat().st_size / 1e6:.2f} Mo)")
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
