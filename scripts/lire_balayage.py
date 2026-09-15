"""Ce que le balayage a mesure, et ce qu'il ne dit pas.

    python scripts/lire_balayage.py --etape 1
    python scripts/lire_balayage.py --etape 1 --budget 10000

Le balayage rejoue et ecrit ; il ne choisit rien. Tout le raisonnement est ici,
ce qui permet de le relire sans relancer trois heures de calcul, et de voir d'un
seul endroit toutes les regles de decision.

REGLE DE CHOIX, ECRITE AVANT LES RENDEMENTS. A chaque tranche k >= 3, on retient
la combinaison dont la MOYENNE SUR LES PAIRES est la plus haute sur les trois
tranches PRECEDENTES, et on l'applique a la tranche k. Meme regle que
scripts/valider_en_avant.py, pour que les deux resultats se comparent. Le choix
par CLASSE suit la meme regle, restreinte aux paires de la classe.

LES CLASSES SE CALCULENT SUR LE PASSE SEUL. A la tranche k, les paires sont
rangees par volatilite mesuree sur les tranches 0..k-1, et coupees a la mediane :
agitees au-dessus, calmes en dessous. Aucun rendement n'entre dans ce calcul, et
aucune tranche future.

DEUX CRITERES DE VOLATILITE, ET TOUT EST RELU SOUS CHACUN. L'amplitude moyenne a
cinq minutes et l'amplitude mediane a l'heure sont toutes deux stables dans le
temps, mais elles ne classent pas les memes paires ensemble. Choisir entre elles
d'apres les rendements serait exactement la faute que cette etude evite. On
publie donc les deux, et on regarde si la conclusion change.

QUATRE REPERES, parce qu'un rendement seul ne dit rien :
  - le TEMOIN, le reglage du direct, applique partout sans jamais choisir ;
  - le TIRAGE AU SORT, une combinaison prise au hasard a chaque decision : si le
    choix ne le bat pas, choisir ne sert a rien ;
  - NE RIEN FAIRE, zero pour cent, capital en caisse ;
  - ACHETER ET GARDER, la derive de la paire sur la tranche.

XRP EST EXCLUE DE TOUTE MOYENNE (decision 4 du balayage) : son historique ne
couvre que dix des vingt-deux tranches, et une moyenne qui change de panier au
milieu du tableau ne se compare a rien. Elle garde sa fiche, qui le dit.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as stt
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#  Les colonnes du fichier de marche, nommees une fois pour toutes.
MOY5, MED1H, PLAT5, PLAT1H, DERIVE = 0, 1, 2, 3, 4
CRITERES = {MOY5: "amplitude moyenne a 5 min", MED1H: "amplitude mediane a l'heure"}


def pct(x: float) -> str:
    return f"{x * 100:+6.2f}%"


def corr_rang(a: list[float], b: list[float]) -> float:
    """Correlation de Spearman, a la main : on n'installe pas scipy pour ca."""
    def rangs(v):
        r = [0.0] * len(v)
        for pos, i in enumerate(sorted(range(len(v)), key=lambda j: v[j])):
            r[i] = float(pos)
        return r
    ra, rb = rangs(a), rangs(b)
    n = len(a)
    if n < 2:
        return float("nan")
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else float("nan")


def resume_spec(c: dict) -> str:
    f = c["espacement"][:4] + (f"^{c['courbure']:g}" if c["espacement"] == "puissance" else "")
    return (f"{c['paliers']:>2} paliers  r={c['ratio']:<4g} prof={c['profondeur']:<5.0%} "
            f"obj={c['objectif_net']:<5.1%} dep={c['depart_sous']:<5.1%} {f}")


class Etude:
    def __init__(self, d: dict, ib: int, marche: dict):
        self.d = d
        self.ib = ib
        self.budget = d["budgets"][ib]
        self.paires = d["paires"]
        self.cbs = d["combinaisons"]
        self.n_blocs = d["n_blocs"]
        self.appr = d["apprentissage"]
        self.frais = d["frais"]
        self.partielles = set(d["partielles"])
        self.pleines = [s for s in self.paires if s not in self.partielles]
        self.marche = marche
        #  LE TEMOIN SE RECONNAIT A SA SPECIFICATION, JAMAIS A SON ETIQUETTE.
        #  L'etiquette « temoin » n'existe que dans le plan de l'etape 1 ; aux
        #  etapes suivantes les origines sont « fixe:… », « vol:… », « meca:… »,
        #  et un next(..., 0) retombait EN SILENCE sur la combinaison d'indice 0.
        #  Le tableau annoncait alors un « temoin » qui etait une geometrie
        #  quelconque : +1,55 % a l'etape 2 contre +0,21 % a l'etape 1, pour ce
        #  qui devait etre le meme reglage. Un repere faux est pire qu'un repere
        #  absent, donc on compare les specifications et, a defaut, on l'avoue.
        cle = json.dumps(d["temoin"], sort_keys=True)
        self.i_temoin = next(
            (i for i, c in enumerate(self.cbs)
             if json.dumps({k: v for k, v in c.items() if not k.startswith("_")},
                           sort_keys=True) == cle), None)
        self.perf: dict[tuple[int, int, int], tuple] = {}
        for ligne in d["matrice"]:
            if ligne[2] == ib:
                self.perf[(ligne[0], ligne[1], ligne[3])] = ligne[4:]

    # ------------------------------------------------------------ acces
    def p(self, ip: int, i: int, k: int):
        r = self.perf.get((ip, i, k))
        return r[0] if r else None

    def duree(self, ip: int, i: int, k: int):
        r = self.perf.get((ip, i, k))
        return r[7] if r and r[7] >= 0 else None

    def moyenne(self, i: int, blocs, paires):
        v = [x for k in blocs for ip, s in enumerate(self.paires) if s in paires
             for x in (self.p(ip, i, k),) if x is not None]
        return sum(v) / len(v) if v else None

    # ------------------------------------------------------------ classes
    def classes(self, k: int, critere: int) -> dict[str, str]:
        """Les paires rangees par volatilite VUE, coupees a la mediane.

        LE SEUIL SE CALCULE SUR LES SEULES PAIRES PLEINES, l'appartenance se
        lit pour tout le monde. XRP est exclue de toutes les moyennes parce que
        son historique ne couvre que dix tranches sur vingt-deux (decision 4 du
        balayage) ; la laisser peser sur la mediane qui coupe les classes
        l'aurait fait rentrer par la fenetre — le seuil aurait porte sur dix
        paires avant sa date et sur neuf apres, et les classes auraient change
        de definition au milieu du tableau. Elle garde sa classe, lue contre un
        seuil auquel elle n'a pas contribue.
        """
        vol = {}
        for s in self.paires:
            v = [self.marche[s][str(j)][critere]
                 for j in range(k) if str(j) in self.marche.get(s, {})]
            if v:
                vol[s] = sum(v) / len(v)
        refs = [v for s, v in vol.items() if s in self.pleines]
        if len(refs) < 2:
            return {}
        seuil = stt.median(refs)
        return {s: ("agitee" if v >= seuil else "calme") for s, v in vol.items()}

    def choisir(self, blocs, paires) -> int | None:
        notes = [(m, i) for i in range(len(self.cbs))
                 for m in (self.moyenne(i, blocs, paires),) if m is not None]
        return max(notes)[1] if notes else None


def section_marche(e: Etude) -> None:
    print("\n" + "=" * 78)
    print("1. LE MARCHE — ce que les tranches etaient, sans aucune strategie")
    print("=" * 78)
    print(f"  {'paire':<7} {'tranches':>8} {'moy 5min':>10} {'med 1h':>9} "
          f"{'plates 5m':>10} {'derive moy':>11} {'baissieres':>11}")
    for s in sorted(e.paires, key=lambda x: -(
            sum(v[MED1H] for v in e.marche[x].values()) / max(1, len(e.marche[x])))):
        bl = e.marche.get(s, {})
        if not bl:
            continue
        v = list(bl.values())
        n = len(v)
        der = [x[DERIVE] for x in v]
        marque = "  (partielle)" if s in e.partielles else ""
        print(f"  {s.split('/')[0]:<7} {n:>8} {sum(x[MOY5] for x in v) / n * 100:9.4f}% "
              f"{sum(x[MED1H] for x in v) / n * 100:8.4f}% "
              f"{sum(x[PLAT5] for x in v) / n * 100:9.1f}% {pct(sum(der) / n):>11} "
              f"{sum(1 for x in der if x < 0):>7}/{n}{marque}")

    for crit, nom in CRITERES.items():
        ks = sorted({int(k) for s in e.paires for k in e.marche.get(s, {})})
        cs = []
        for a, b in zip(ks, ks[1:]):
            com = [s for s in e.paires
                   if str(a) in e.marche.get(s, {}) and str(b) in e.marche.get(s, {})]
            if len(com) >= 6:
                cs.append(corr_rang([e.marche[s][str(a)][crit] for s in com],
                                    [e.marche[s][str(b)][crit] for s in com]))
        if cs:
            print(f"  {nom:<30} stabilite du classement : {sum(cs) / len(cs):+.2f} "
                  f"(min {min(cs):+.2f})")
    print("  ce sont ces deux chiffres, et eux seuls, qui autorisent a classer d'avance.")


def section_duree(e: Etude) -> None:
    print("\n" + "=" * 78)
    print("2. LA DUREE — la seule condition posee : des cycles sous quarante-huit heures")
    print("=" * 78)
    stats = []
    for i in range(len(e.cbs)):
        ds = [d for ip, s in enumerate(e.paires) if s in e.pleines
              for k in range(e.n_blocs) for d in (e.duree(ip, i, k),) if d is not None]
        m = e.moyenne(i, range(e.n_blocs), e.pleines)
        if ds and m is not None:
            stats.append((stt.median(ds), m, i))
    if not stats:
        print("  aucune combinaison n'a produit un seul cycle")
        return
    stats.sort()
    s48 = [x for x in stats if x[0] < 48]
    s10 = [x for x in stats if x[0] < 10]
    print(f"  {len(s48)}/{len(stats)} combinaisons ont une duree mediane sous 48 h ; "
          f"{len(s10)} sous 10 h")
    if s48:
        print(f"  parmi les {len(s48)} sous 48 h, {sum(1 for x in s48 if x[1] > 0)} "
              f"rendent positif sur l'ensemble des tranches")
    print("\n  les dix plus rapides :")
    print(f"    {'mediane':>8} {'rendement':>10}   reglage")
    for med, m, i in stats[:10]:
        print(f"    {med:>7.1f}h {pct(m):>10}   {resume_spec(e.cbs[i])}")
    print("\n  les dix qui rendent le plus, duree comprise :")
    for med, m, i in sorted(stats, key=lambda x: -x[1])[:10]:
        print(f"    {med:>7.1f}h {pct(m):>10} {' ' if med < 48 else '!'} {resume_spec(e.cbs[i])}")
    print("    ( ! = mediane au-dessus de 48 h )")
    print("\n  « mediane » est ici une MEDIANE DE MEDIANES : la duree mediane des cycles")
    print("  est calculee dans chaque tranche et pour chaque paire, puis on prend la")
    print("  mediane de ces valeurs. Ce n'est pas la mediane de tous les cycles mis")
    print("  ensemble : une tranche qui en produit quarante pesait alors vingt fois plus")
    print("  qu'une tranche qui en produit deux, et les tranches calmes — celles ou le")
    print("  capital reste justement bloque — disparaissaient du chiffre.")
    mt = (e.moyenne(e.i_temoin, range(e.n_blocs), e.pleines)
          if e.i_temoin is not None else None)
    dt = next((x[0] for x in stats if x[2] == e.i_temoin), None)
    if mt is not None:
        print(f"\n  le temoin : {pct(mt)} pour une mediane de "
              f"{dt:.1f} h" if dt else f"\n  le temoin : {pct(mt)}")


def section_avant(e: Etude):
    print("\n" + "=" * 78)
    print("3. LE CHOIX EN AVANT — choisir sur le passe, encaisser sur la suite")
    print("=" * 78)
    rng = random.Random(7)
    noms = {"choix": "choix global",
            "classe0": f"choix par classe ({CRITERES[MOY5]})",
            "classe1": f"choix par classe ({CRITERES[MED1H]})",
            "temoin": "temoin (reglage du direct)",
            "hasard": "tirage au sort",
            "rien": "ne rien faire",
            "garder": "acheter et garder",
            "parfait": "le meilleur apres coup"}
    bilans = {n: [] for n in noms}
    retenus = []
    for k in range(e.appr, e.n_blocs):
        passe = range(max(0, k - 3), k)
        i_glob = e.choisir(passe, e.pleines)
        if i_glob is None:
            continue
        i_hasard = rng.randrange(len(e.cbs))
        par_crit = {}
        for crit in (MOY5, MED1H):
            cl = e.classes(k, crit)
            par_crit[crit] = (cl, {c: e.choisir(passe, [s for s in e.pleines
                                                        if cl.get(s) == c])
                                  for c in ("agitee", "calme")})
        for ip, s in enumerate(e.paires):
            if s not in e.pleines or e.p(ip, i_glob, k) is None:
                continue
            bilans["choix"].append(e.p(ip, i_glob, k))
            for crit in (MOY5, MED1H):
                cl, ch = par_crit[crit]
                ic = ch.get(cl.get(s)) or i_glob
                v = e.p(ip, ic, k)
                bilans[f"classe{crit}"].append(v if v is not None else e.p(ip, i_glob, k))
            for nom, i in (("temoin", e.i_temoin), ("hasard", i_hasard)):
                if i is None:
                    continue
                x = e.p(ip, i, k)
                if x is not None:
                    bilans[nom].append(x)
            m = e.marche.get(s, {}).get(str(k))
            if m:
                #  Acheter et garder paie DEUX passages de frais, comme tout le
                #  monde : un a l'entree, un a la sortie. Comparer une derive
                #  brute a des rendements nets avantagerait le repere de vingt
                #  centiemes de point par tranche, systematiquement et dans le
                #  meme sens — le genre de biais qui ne se voit jamais.
                bilans["garder"].append((1 + m[DERIVE]) * (1 - e.frais) ** 2 - 1)
            best = max((x for i in range(len(e.cbs))
                        for x in (e.p(ip, i, k),) if x is not None), default=None)
            if best is not None:
                bilans["parfait"].append(best)
        retenus.append((k, i_glob))
    bilans["rien"] = [0.0] * len(bilans["choix"])

    print(f"  {len(bilans['choix'])} decisions en avant "
          f"({e.n_blocs - e.appr} tranches x {len(e.pleines)} paires), "
          f"budget {e.budget:.0f} EUR\n")
    print(f"    {'repere':<42} {'moyenne':>9} {'mediane':>9} {'part > 0':>9}")
    for n in ("choix", "classe0", "classe1", "temoin", "hasard", "rien",
              "garder", "parfait"):
        v = bilans[n]
        if v:
            print(f"    {noms[n]:<42} {pct(sum(v) / len(v)):>9} "
                  f"{pct(stt.median(v)):>9} {sum(1 for x in v if x > 0) / len(v) * 100:>8.0f}%")

    def moy(n):
        return sum(bilans[n]) / len(bilans[n]) * 100 if bilans[n] else 0.0
    print(f"\n  choisir contre tirer au sort            : {moy('choix') - moy('hasard'):+.2f} point par tranche")
    if bilans["temoin"]:
        print(f"  choisir contre le temoin                : "
              f"{moy('choix') - moy('temoin'):+.2f} point")
    else:
        #  Une soustraction contre une liste vide vaut la valeur elle-meme et se
        #  lirait comme un ecart au temoin : on se tait plutot que de le laisser
        #  croire. Le temoin n'est present que dans les etapes dont le plan le
        #  pose ; l'etape 2 a ete lancee avant que ce soit garanti.
        print("  le temoin ne figure pas dans le lot de cette etape :")
        print("  aucun ecart a lui n'est calculable, et aucun n'est affiche")
    for crit in (MOY5, MED1H):
        print(f"  par classe contre global ({CRITERES[crit]:<26}) : "
              f"{moy(f'classe{crit}') - moy('choix'):+.2f} point")
    print("  ce sont ces ecarts qui sont le resultat. Le reste n'est que description.")
    #  Les NOMBRES sont rendus, jamais le texte. La page et le compte rendu les
    #  prennent ici plutot que de relire ce qui vient d'etre imprime : une
    #  colonne qui s'elargit d'un caractere suffirait a faire dire autre chose a
    #  une expression reguliere, et personne ne le verrait avant publication.
    #  bilans[n] est nomme EXPLICITEMENT a chaque tour. Une premiere version
    #  ecrivait sum(v)/len(v) en s'appuyant sur le v de la boucle d'impression
    #  ci-dessus : la variable fuitait, et les huit reperes portaient tous la
    #  valeur du dernier — celle du « meilleur apres coup ». La page affichait
    #  donc « 0,00 point » partout, ce qui aurait ete lu comme « choisir ne
    #  change rien » alors que la mesure dit « choisir coute 1,17 point ».
    def stat(n):
        w = bilans[n]
        return {"nom": noms[n], "cle": n, "n": len(w),
                "moyenne": sum(w) / len(w), "mediane": stt.median(w),
                "part_positive": sum(1 for x in w if x > 0) / len(w)}
    return retenus, [stat(n) for n in ("choix", "classe0", "classe1", "temoin",
                                       "hasard", "rien", "garder", "parfait")
                     if bilans[n]]


def section_baisse(e: Etude) -> dict:
    print("\n" + "=" * 78)
    print("4. LES MARCHES EN BAISSE — la demande porte sur eux, et sur eux seuls")
    print("=" * 78)
    couples = [(ip, k) for ip, s in enumerate(e.paires) if s in e.pleines
               for k in range(e.appr, e.n_blocs)
               if e.marche.get(s, {}).get(str(k), [0] * 6)[DERIVE] < 0]
    out: dict = {"couples": len(couples)}
    if not couples:
        print("  aucune tranche baissiere")
        return out
    print(f"  {len(couples)} couples (paire, tranche) ou le prix a fini plus bas "
          f"qu'il n'a commence")
    stats = []
    for i in range(len(e.cbs)):
        v = [x for ip, k in couples for x in (e.p(ip, i, k),) if x is not None]
        ds = [x for ip, k in couples for x in (e.duree(ip, i, k),) if x is not None]
        if v:
            stats.append((sum(v) / len(v), stt.median(ds) if ds else -1.0, i,
                          sum(1 for x in v if x > 0) / len(v)))
    stats.sort(reverse=True)
    rang = next((r for r, x in enumerate(stats, 1) if x[2] == e.i_temoin), None)
    tm = next((x[0] for x in stats if x[2] == e.i_temoin), None)
    if tm is not None:
        print(f"  le temoin rend {pct(tm)} en marche baissier, rang {rang}/{len(stats)}")
    print(f"\n    {'rendement':>10} {'mediane':>8} {'gagnantes':>10}   reglage")
    for m, med, i, part in stats[:12]:
        d = f"{med:>7.1f}h" if med >= 0 else "      —"
        print(f"    {pct(m):>10} {d} {part * 100:>9.0f}%   {resume_spec(e.cbs[i])}")

    #  LA QUESTION QUI DECIDE : ce classement tient-il dans le temps ?
    #
    #  Le choix en avant echoue globalement — il coute 1,17 point contre un
    #  tirage au sort. Mais rien ne dit qu'il echoue AUSSI en marche baissier :
    #  si les memes reglages y reviennent en tete d'une periode a l'autre, alors
    #  « choisir pour la baisse » serait possible la ou « choisir » ne l'est pas.
    #  On coupe donc les tranches baissieres en deux moities chronologiques, on
    #  classe les combinaisons dans chacune, et on compare les deux classements.
    #  C'est exactement le test qui a autorise les classes de volatilite (+0,77)
    #  et interdit les classes de rendement (-0,10).
    milieu = (e.appr + e.n_blocs) // 2
    tot = [(ip, k) for ip, k in couples]
    moities = ([(ip, k) for ip, k in tot if k < milieu],
               [(ip, k) for ip, k in tot if k >= milieu])
    if all(len(x) >= 20 for x in moities):
        rangs = []
        for lot in moities:
            v = []
            for i in range(len(e.cbs)):
                x = [y for ip, k in lot for y in (e.p(ip, i, k),) if y is not None]
                v.append(sum(x) / len(x) if x else None)
            rangs.append(v)
        ok = [i for i in range(len(e.cbs))
              if rangs[0][i] is not None and rangs[1][i] is not None]
        c = corr_rang([rangs[0][i] for i in ok], [rangs[1][i] for i in ok])
        #  Le meilleur de la premiere moitie, rejoue sur la seconde : la seule
        #  lecture qui compte vraiment, puisque c'est ce qu'on aurait fait.
        best = max(ok, key=lambda i: rangs[0][i])
        med = stt.median([rangs[1][i] for i in ok])
        print(f"\n  STABILITE DU CLASSEMENT EN BAISSE, {len(moities[0])} couples avant la "
              f"tranche {milieu}, {len(moities[1])} apres :")
        print(f"    correlation des rangs entre les deux moities : {c:+.2f}")
        print(f"    le meilleur de la 1re moitie ({pct(rangs[0][best])}) rend "
              f"{pct(rangs[1][best])} sur la 2e ; la mediane des combinaisons y rend "
              f"{pct(med)}")

        #  ET VOICI POURQUOI CE CHIFFRE NE VAUT PAS CE QU'IL SEMBLE VALOIR.
        #
        #  Une correlation de rang proche de 1 ferait croire a un savoir-faire
        #  reproductible. Avant de le croire, on demande CONTRE QUOI ce classement
        #  est correle. S'il l'est contre l'ENGAGEMENT — la part du budget qui
        #  travaille — alors « bien se comporter en baisse » ne veut dire qu'une
        #  chose : ne pas acheter. Ce n'est pas une strategie, c'est une position
        #  plus petite, et on l'obtiendrait aussi bien en divisant la mise par
        #  vingt. Ce test-la doit rester colle au chiffre de stabilite : separes,
        #  le premier se lit comme une bonne nouvelle.
        eng, blo, rd = [], [], []
        for i in ok:
            v = [e.perf[(ip, i, k)] for ip, k in tot if (ip, i, k) in e.perf]
            if v:
                rd.append(sum(x[0] for x in v) / len(v))
                eng.append(sum(x[4] for x in v) / len(v))
                blo.append(sum(x[3] for x in v) / len(v))
        if len(rd) > 10:
            ce, cb = corr_rang(rd, eng), corr_rang(rd, blo)
            j = max(range(len(rd)), key=lambda x: rd[x])
            print(f"\n    CONTRE QUOI CE CLASSEMENT EST-IL CORRELE ?")
            print(f"      rendement en baisse contre engagement moyen : {ce:+.2f}")
            print(f"      rendement en baisse contre capital bloque   : {cb:+.2f}")
            print(f"      le mieux classe engage {eng[j]:.1%} du budget ; "
                  f"la mediane en engage {stt.median(eng):.1%}")
            if ce < -0.7:
                print("      Le classement est donc un classement par EXPOSITION, et non")
                print("      par savoir-faire : le mieux place est celui qui n'achete presque")
                print("      pas. Il perd moins parce qu'il risque moins, ce qu'on obtiendrait")
                print("      aussi bien en reduisant la mise. La grille n'a pas d'avantage en")
                print("      marche baissier ; elle n'a que de l'exposition.")
            out["exposition"] = {
                "corr_engagement": round(ce, 3), "corr_bloque": round(cb, 3),
                "engage_meilleur": round(eng[j], 4),
                "engage_median": round(stt.median(eng), 4)}
        out["stabilite"] = {
            "correlation": round(c, 3), "coupure": milieu,
            "avant": len(moities[0]), "apres": len(moities[1]),
            "meilleur_1re": round(rangs[0][best], 5),
            "meilleur_sur_2e": round(rangs[1][best], 5),
            "mediane_2e": round(med, 5)}
    out["temoin"] = round(tm, 5) if tm is not None else None
    out["temoin_rang"] = rang
    out["classement"] = [
        {"i": i, "rendement": round(m, 5), "duree": round(med_, 2) if med_ >= 0 else None,
         "gagnantes": round(part, 4), "spec": resume_spec(e.cbs[i])}
        for m, med_, i, part in stats[:12]]
    return out


def section_fiches(e: Etude, retenus) -> None:
    print("\n" + "=" * 78)
    print("5. LES FICHES — une monnaie, ce qu'elle vaut pour cette methode")
    print("=" * 78)
    cl0 = e.classes(e.n_blocs, MOY5)
    cl1 = e.classes(e.n_blocs, MED1H)
    for ip, s in enumerate(e.paires):
        bl = e.marche.get(s, {})
        if not bl:
            continue
        v = list(bl.values())
        n = len(v)
        av = [x for k, i in retenus for x in (e.p(ip, i, k),) if x is not None]
        tm = ([x for k in range(e.appr, e.n_blocs)
               for x in (e.p(ip, e.i_temoin, k),) if x is not None]
              if e.i_temoin is not None else [])
        best = max(range(len(e.cbs)),
                   key=lambda i: e.moyenne(i, range(e.n_blocs), [s]) or -9.0)
        print(f"\n  {s}")
        if s in e.partielles:
            print(f"    HISTORIQUE PARTIEL : {n} tranches sur {e.n_blocs}. Mesuree comme")
            print(f"    les autres, mais exclue de toutes les moyennes et de tous les choix,")
            print(f"    parce qu'une moyenne qui change de panier ne se compare a rien.")
        print(f"    classe : {cl0.get(s, '?')} a 5 min, {cl1.get(s, '?')} a l'heure   "
              f"(moy 5m {sum(x[MOY5] for x in v) / n * 100:.4f}%, "
              f"med 1h {sum(x[MED1H] for x in v) / n * 100:.4f}%)")
        print(f"    bougies plates : {sum(x[PLAT5] for x in v) / n * 100:.1f}% a 5 min, "
              f"{sum(x[PLAT1H] for x in v) / n * 100:.1f}% a l'heure")
        print(f"    tranches baissieres : {sum(1 for x in v if x[DERIVE] < 0)}/{n}   "
              f"derive moyenne {pct(sum(x[DERIVE] for x in v) / n)}")
        if av and tm:
            print(f"    choix en avant {pct(sum(av) / len(av))} par tranche"
                  f"    temoin {pct(sum(tm) / len(tm))}")
        m = e.moyenne(best, range(e.n_blocs), [s])
        if m is not None:
            print(f"    son meilleur reglage APRES COUP ({pct(m)}) : {resume_spec(e.cbs[best])}")
    print("\n  « bougies plates » = part des bougies dont le haut egale le bas, donc sans")
    print("  une seule transaction. Le rejeu suppose pourtant qu'un ordre limite y aurait")
    print("  ete servi : c'est la principale reserve sur les paires calmes, et elle n'est")
    print("  pas encore bornee.")


def _familles(e: Etude) -> dict[str, list[int]]:
    """Regroupe les combinaisons par GEOMETRIE : le bras temoin et ses variantes.

    Le regroupement se fait sur tout ce qui n'est ni un mecanisme ni une
    normalisation : deux combinaisons qui ne different que par le declencheur
    posent la meme echelle, et c'est ce qui rend leur comparaison APPARIEE.
    """
    #  TOUT CE QUI N'EST PAS LA GEOMETRIE. Un champ oublie ici casse
    #  l'appariement en silence : les deux bras tombent dans deux familles
    #  differentes, plus aucune famille n'a de temoin, et la section annonce
    #  « aucune paire de bras a comparer » sans dire pourquoi. C'est arrive avec
    #  participation_max a l'etape 4.
    varie = {"echelle_vol", "unite_vol", "fenetre_vol", "facteur_min", "facteur_max",
             "devi_chute", "devi_fenetre", "filtre_moyenne", "filtre_sens",
             "confirmation", "suivre_baisse", "sortie_jours", "reancrage_min",
             "participation_max"}
    out: dict[str, list[int]] = {}
    for i, c in enumerate(e.cbs):
        cle = json.dumps({k: v for k, v in sorted(c.items())
                          if not k.startswith("_") and k not in varie})
        out.setdefault(cle, []).append(i)
    return out


def _dispersion(e: Etude, i: int) -> float | None:
    """L'ecart-type des rendements D'UNE PAIRE A L'AUTRE, moyenne sur les tranches.

    C'est LA mesure de l'etape 2. L'hypothese n'est pas « le rendement moyen
    monte » — elle est « les paires cessent d'etre des cas differents ». Un
    rendement moyen peut monter parce qu'une seule paire s'envole ; la
    dispersion, elle, ne peut baisser que si les paires se rapprochent.
    """
    ds = []
    for k in range(e.n_blocs):
        v = [x for ip, s in enumerate(e.paires) if s in e.pleines
             for x in (e.p(ip, i, k),) if x is not None]
        if len(v) >= 4:
            ds.append(stt.pstdev(v))
    return sum(ds) / len(ds) if ds else None


def section_appariee(e: Etude, etiquette: str, base: str) -> dict:
    """Chaque variante contre le bras temoin DE SA PROPRE geometrie.

    Comparer la meilleure variante a la meilleure des temoins comparerait deux
    maxima pris sur des effectifs differents — le piege que la matrice de
    validation de ce depot a deja chiffre. Ici, chaque couple (paire, tranche)
    est compare a LUI-MEME sous l'autre bras, et on moyenne les DIFFERENCES.
    """
    print("\n" + "=" * 78)
    print(f"{etiquette}")
    print("=" * 78)
    fam = _familles(e)
    #  Pour chaque variante, l'ecart apparie contre le bras de base.
    ecarts: dict[str, list[float]] = {}
    #  L'ECART D'ENGAGEMENT, MESURE A COTE DE L'ECART DE RENDEMENT. Un bras qui
    #  rend moins parce qu'il achete moins n'est pas un mauvais bras, c'est une
    #  position plus petite — et on l'obtiendrait en divisant la mise. Sans cette
    #  colonne, les deux se confondent. Ce banc a deja paye la confusion une
    #  fois : le « meilleur reglage en marche baissier » etait celui qui
    #  n'achetait presque pas, et rien ne le disait.
    engage: dict[str, list[float]] = {}
    disp: dict[str, list[tuple[float, float]]] = {}
    duree: dict[str, list[float]] = {}
    n_geo = 0
    for cle, idx in fam.items():
        ref = next((i for i in idx
                    if (e.cbs[i].get("_origine") or "").startswith(base)), None)
        if ref is None:
            continue
        n_geo += 1
        dref = _dispersion(e, ref)
        for i in idx:
            nom = variante(e.cbs[i], base)
            if nom is None:
                continue
            paires_ok = [(ip, k) for ip, s in enumerate(e.paires) if s in e.pleines
                         for k in range(e.n_blocs)
                         if e.p(ip, i, k) is not None and e.p(ip, ref, k) is not None]
            if not paires_ok:
                continue
            ecarts.setdefault(nom, []).extend(
                e.p(ip, i, k) - e.p(ip, ref, k) for ip, k in paires_ok)
            engage.setdefault(nom, []).extend(
                e.perf[(ip, i, k)][4] - e.perf[(ip, ref, k)][4] for ip, k in paires_ok)
            d = _dispersion(e, i)
            if d is not None and dref is not None:
                disp.setdefault(nom, []).append((dref, d))
            ds = [x for ip, k in paires_ok for x in (e.duree(ip, i, k),) if x is not None]
            if ds:
                duree.setdefault(nom, []).append(stt.median(ds))
    if not ecarts:
        print("  aucune paire de bras a comparer")
        return {"geometries": 0, "variantes": []}
    print(f"  {n_geo} geometries, chacune comparee A ELLE-MEME sous chaque variante\n")
    print(f"    {'variante':<26} {'ecart apparie':>14} {'gagne':>7} "
          f"{'engagement':>12} {'dispersion':>12} {'duree med':>10}")
    lignes = []
    for nom, v in ecarts.items():
        m = sum(v) / len(v)
        gagne = sum(1 for x in v if x > 0) / len(v)
        d = disp.get(nom, [])
        dv = (sum(b for _, b in d) / len(d) / (sum(a for a, _ in d) / len(d))
              if d and sum(a for a, _ in d) else None)
        du = stt.median(duree[nom]) if duree.get(nom) else None
        g = engage.get(nom)
        lignes.append((m, nom, gagne, dv, du, len(v),
                       sum(g) / len(g) if g else None))
    for m, nom, gagne, dv, du, n, en in sorted(lignes, reverse=True):
        print(f"    {nom:<26} {m * 100:>+13.2f}pt {gagne * 100:>6.0f}% "
              f"{(format(en * 100, '+.1f') + 'pt') if en is not None else '—':>12} "
              f"{('x' + format(dv, '.2f')) if dv else '—':>12} "
              f"{(format(du, '.1f') + 'h') if du else '—':>10}")
    print("\n  « ecart apparie » : la meme geometrie, la meme paire, la meme tranche,")
    print("  avec et sans la variante. C'est une difference, jamais deux moyennes mises")
    print("  cote a cote. « dispersion » : ecart-type des rendements D'UNE PAIRE A")
    print("  L'AUTRE, rapporte a celui du bras de base — sous 1,00, les paires se")
    print("  ressemblent davantage, ce qui est l'hypothese meme de l'etape 2.")
    #  LA CORRELATION ENTRE LES DEUX ECARTS REPOND A LA SEULE OBJECTION QUI VAUT.
    #  Proche de 1, les variantes ne feraient que reduire la position et leur
    #  « resultat » n'en serait pas un. Faible, elles changent vraiment ce qui
    #  est achete, a exposition egale — et l'ecart de rendement leur appartient.
    xs = [x[0] for x in lignes if x[6] is not None]
    ys = [x[6] for x in lignes if x[6] is not None]
    ce = corr_rang(xs, ys) if len(xs) > 3 else None
    if ce is not None:
        print("")
        print(f"  correlation entre l'ecart de RENDEMENT et l'ecart d'ENGAGEMENT :"
              f" {ce:+.2f}")
        print("  Proche de 1, les variantes ne feraient que reduire la position et leur")
        print("  resultat n'en serait pas un. Faible, elles changent vraiment ce qui est")
        print("  achete, a exposition egale, et l'ecart de rendement leur appartient.")
    return {"geometries": n_geo, "base": base,
            "corr_engagement": round(ce, 3) if ce is not None else None,
            "variantes": [
        {"nom": nom, "ecart": round(m, 6), "gagne": round(gagne, 4),
         "dispersion": round(dv, 3) if dv else None,
         "engagement": round(en, 6) if en is not None else None,
         "duree": round(du, 2) if du else None, "n": n}
        for m, nom, gagne, dv, du, n, en in sorted(lignes, reverse=True)]}


def variante(c: dict, base: str) -> str | None:
    """Le nom court d'un bras, ou None si c'est le bras de base lui-meme."""
    o = c.get("_origine") or ""
    if o.startswith(base):
        return None
    #  UNE ORIGINE VAUT « <prefixe>:<nom>:<famille> », ET LE NOM PEUT CONTENIR
    #  DES DEUX-POINTS : « b:2 % sur 6 h ». Prendre p[1] ecrasait les quatre
    #  reglages du declencheur (b) en une seule ligne, et les trois plafonds de
    #  l'etape 4 en une seule aussi — la moyenne affichee melangeait alors trois
    #  hypotheses d'execution differentes. On rend donc tout ce qui se trouve
    #  entre le prefixe et la famille, quel que soit le prefixe.
    p = o.split(":")
    if p[0] == "vol":
        return f"{p[1]} × {c.get('unite_vol')}"
    if len(p) > 2:
        return ":".join(p[1:-1])
    return p[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--etape", type=int, default=1)
    ap.add_argument("--fichier", default=None)
    ap.add_argument("--marche", default="data/etudes/marche.json")
    ap.add_argument("--budget", type=float, default=None)
    args = ap.parse_args()

    f = RACINE / (args.fichier or f"data/etudes/balayage-{args.etape}.json")
    d = json.loads(f.read_text(encoding="utf-8"))
    mf = RACINE / args.marche
    if not mf.exists():
        raise SystemExit(f"{args.marche} absent ; lance scripts/mesurer_marche.py")
    m = json.loads(mf.read_text(encoding="utf-8"))
    #  LES DEUX FICHIERS DOIVENT DECOUPER LES MEMES JOURS. Les robots ecrivent
    #  des bougies en continu ; deux scripts lances a une heure d'ecart peuvent
    #  partir de t0 differents, et la tranche k de l'un ne serait plus la
    #  tranche k de l'autre. On refuse plutot que de comparer a cote.
    if m["t0"] != d["t0"] or m["n_blocs"] != d["n_blocs"]:
        raise SystemExit(
            f"fenetres differentes : balayage t0={d['t0']} n={d['n_blocs']}, "
            f"marche t0={m['t0']} n={m['n_blocs']}. Relance mesurer_marche.py "
            f"avec --herite {f.relative_to(RACINE)}")

    for b in (d["budgets"] if args.budget is None else [args.budget]):
        e = Etude(d, d["budgets"].index(b), m["marche"])
        print("\n" + "#" * 78)
        print(f"#  ETAPE {d['etape']} — BUDGET {b:.0f} EUR PAR PAIRE, "
              f"{len(e.cbs)} combinaisons, {d['bloc_jours']} jours par tranche")
        print("#" * 78)
        section_marche(e)
        section_duree(e)
        #  L'etape 2 et l'etape 3 posent une question APPARIEE : la meme
        #  geometrie, avec et sans. Elle passe avant le choix en avant, parce
        #  qu'elle ne depend d'aucune regle de choix et qu'elle est donc la plus
        #  difficile a se raconter.
        if d["etape"] == 2:
            section_appariee(
                e, "2 bis. L'ECHELLE EN UNITES DE VOLATILITE — la meme geometrie, "
                   "normalisee ou non", "fixe")
        elif d["etape"] == 3:
            section_appariee(
                e, "2 bis. LES MECANISMES — un a la fois, contre la meme geometrie nue",
                "meca:temoin")
        elif d["etape"] == 4:
            section_appariee(
                e, "2 bis. L'ENCADREMENT — le meme reglage, sans plafond de volume "
                   "puis sous trois plafonds", "sans")
        r, _reperes = section_avant(e)
        section_baisse(e)
        section_fiches(e, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
