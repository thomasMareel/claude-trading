"""Ce qu'UNE echelle donne sur DIX paires, sur la fenetre de la page des simulations.

    python scripts/exporter_paires.py

LA QUESTION. Le paper trading fait tourner depuis le 13 septembre 2026 la meme
echelle a huit barreaux sur dix paires, mille euros chacune, pour voir ce que la
PAIRE change. Le direct mettra des mois a repondre. Le passe, lui, repond tout
de suite : ce script rejoue exactement cette configuration, sur exactement les
quatre cents jours que la page des simulations affiche deja.

UNE SEULE CONFIGURATION. Rien n'est balaye, rien n'est choisi, rien n'est
classe pour etre suivi. Le panier des dix paires vient du classement de
LIQUIDITE — un critere connu d'avance, jamais un rendement — et il ne bouge pas,
quoi que dise le tableau produit ici.

---------------------------------------------------------------------------
TROIS CHOSES QUE CETTE MESURE N'EST PAS, et qu'il faut ecrire avant de lire ses
chiffres. Les trois ont ete verifiees, pas supposees.

1. CE N'EST PAS UNE MESURE NEUVE. La fenetre de la page des simulations va du
   2025-08-02 21:00 au 2026-09-06 21:00 UTC. L'etude a neuf cents jours
   (scripts/etudier_dix_paires.py) decoupe la meme histoire en neuf blocs de
   cent jours, et ses blocs b5 a b8 couvrent 2025-08-02 20:55 -> 2026-09-06
   20:55. CINQ MINUTES d'ecart aux deux bouts : c'est le meme dernier tiers,
   rejoue autrement. Deux lectures de la meme donnee ne se confirment pas l'une
   l'autre, et le mot « confirme » est donc interdit ici.

2. CE N'EST PAS LE MEME PROTOCOLE, et la difference se mesure. L'etude a neuf
   cents jours REMET L'ECHELLE A NEUF au debut de chaque bloc ; ici elle court
   sans interruption sur quatre cents jours, comme le fait le vrai robot. Une
   remise a neuf est une intervention, et elle est favorable : elle libere un
   capital qu'une echelle pleine garde immobilise. Le script chiffre l'ecart
   lui-meme, paire par paire, plutot que de laisser croire que la methode a
   change de comportement alors que c'est le protocole qui a change.

3. LA FENETRE EST BAISSIERE. Sur ces quatre cents jours, « ne rien faire » perd
   en moyenne un tiers. Un amortisseur y parait mecaniquement bon. Le chiffre de
   comparaison est donc publie paire par paire, jamais la seule performance.

---------------------------------------------------------------------------
LES EPREUVES. Trois des quatre epreuves de la page des simulations se calculent
sur une configuration unique — le voisinage perturbe SES parametres, l'epreuve
des paires prend le minimum sur SES paires, celle des trimestres decoupe SA
fenetre. Elles sont donc calculees, et leur resultat est publie tel quel, echec
compris. La quatrieme, celle des resolutions, est INDISPONIBLE : sept des dix
paires n'ont ni bougie horaire ni bougie a la minute dans la base. On l'ecrit
« indisponible » avec son motif chiffre, jamais « non applicable », et jamais
vide.

Et aucune des trois ne prouve quoi que ce soit du futur : elles decoupent le
meme passe. Celle des trimestres, en particulier, N'EST PAS un controle
supplementaire ici — ses quatre trimestres sont exactement les blocs b5 a b8 de
l'etude a neuf cents jours.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

#  Le voisinage, le plancher et l'arrondi viennent de l'exporteur principal :
#  deux definitions du meme seuil finiraient par diverger, et c'est precisement
#  ce que ce depot a deja paye deux fois.
from exporter_simulations import PLANCHER, VOISINAGE, arrondi, bougies, grille_reguliere  # noqa: E402

HEURE = 3_600_000
PAS_SIM = "5m"
PAR_HEURE = 12
PAR_JOUR = 24 * PAR_HEURE

#  Recopie du profil « 8paliers_concentre » de scripts/paper_grille.py. Une copie
#  et non un import : ce module-la ouvre la base et le reseau au chargement.
#  La copie n'est pas laissee a la confiance — verifier_reglage() la compare au
#  reglage que le robot PUBLIE, c'est-a-dire a celui qui tourne vraiment.
REGLAGE = dict(profondeur=0.51, paliers=8, ratio=1.6, objectif_net=0.02,
               depart_sous=0.02, espacement="puissance", courbure=2.0,
               suivre_hausse=True, vente_meme_bougie=False, mise_min=12.0)
PUBLIE = "docs/data/paper_grille_8paliers_concentre.json"


def verifier_reglage() -> None:
    """Refuse de mesurer autre chose que ce qui tourne.

    Une etude qui annonce « exactement le reglage du direct » et qui en mesure un
    autre ne vaut rien, et rien ne le signalerait. Le robot publie le sien a
    chaque cycle : on compare, champ par champ.
    """
    f = RACINE / PUBLIE
    if not f.exists():
        print(f"  {PUBLIE} absent : le reglage n'a pas pu etre confronte au direct")
        return
    vivant = json.loads(f.read_text(encoding="utf-8")).get("reglage", {})
    ecarts = {k: (v, vivant.get(k)) for k, v in REGLAGE.items()
              if k in vivant and vivant[k] != v}
    absents = [k for k in REGLAGE if k not in vivant]
    if ecarts or absents:
        lignes = "\n".join(f"    {k:<18} ici={a!r:<10} en direct={b!r}"
                           for k, (a, b) in sorted(ecarts.items()))
        raise SystemExit(
            "le reglage mesure ici n'est pas celui qui tourne :\n" + lignes
            + (f"\n    champs absents du direct : {absents}" if absents else "")
            + "\n    Corrige la copie, ou explique l'ecart avant de publier.")
    print(f"  reglage confronte au direct ({PUBLIE}) : identique")


def fenetre() -> tuple[int, int]:
    """La fenetre est HERITEE de la page des simulations, jamais recalculee.

    Deux pages qui annoncent « quatre cents jours » doivent porter les MEMES
    quatre cents jours. Et surtout pas une fenetre glissante : la base contient
    des bougies posterieures au demarrage du paper trading a dix paires, et une
    fenetre qui avance donnerait au robot en cours de mesure des jours qu'il n'a
    pas encore vecus.
    """
    m = json.loads((RACINE / "docs/simulations.json").read_text(encoding="utf-8"))["meta"]
    return int(m["t0"]), int(m["heures"])


def paires_du_panier() -> list[str]:
    """Le panier vient du classement de liquidite archive, jamais d'une recopie."""
    f = RACINE / "docs/archives/liquidite.json"
    if not f.exists():
        raise SystemExit("docs/archives/liquidite.json absent ; "
                         "lance d'abord scripts/classer_liquidite.py")
    d = json.loads(f.read_text(encoding="utf-8"))
    return list(d["retenues"])


def serie(st: Storage, s: str, t0: int, n: int) -> list[tuple]:
    """Les bougies de cinq minutes, decoupees sur la fenetre et verifiees.

    Le decoupage n'est pas une precaution de style : charger mille jours pendant
    qu'on en affiche quatre cents a deja produit, sur ce depot, des resultats
    annonces sous un titre qui en disait autre chose.
    """
    fin = t0 + n * HEURE
    b = [x for x in bougies(st, s, PAS_SIM) if t0 <= x[0] < fin]
    if not b:
        raise SystemExit(f"{s} : aucune bougie de cinq minutes dans la fenetre")
    if b[0][0] != t0:
        raise SystemExit(f"{s} : la serie commence a {b[0][0]}, la fenetre a {t0}")
    if len(b) != n * PAR_HEURE:
        raise SystemExit(f"{s} : {len(b)} bougies pour {n * PAR_HEURE} attendues")
    grille_reguliere(b, HEURE // PAR_HEURE)
    return b


def journalier(r: dict, b: list[tuple], budget: float, frais: float) -> dict:
    """Trois series quotidiennes : ce que vaut le portefeuille, ce que vaudrait
    l'argent laisse tranquille, et le pic d'engagement du jour.

    Le MAXIMUM d'engagement et non la moyenne : une moyenne journaliere lisserait
    justement les pics, qui sont le fait le plus important a montrer.
    """
    eq, dep = r["equity"], r["deploiement"]
    ouverture = b[0][1]
    equity, hold, eng = [], [], []
    for j in range(0, len(b), PAR_JOUR):
        tr_eq = eq[j:j + PAR_JOUR]
        tr_dep = dep[j:j + PAR_JOUR]
        tr_b = b[j:j + PAR_JOUR]
        if not tr_eq or not tr_b:
            continue
        equity.append(arrondi(tr_eq[-1][1], 2))
        #  le repere honnete : acheter a l'ouverture, ne rien faire, payer les
        #  deux frais de l'aller-retour — exactement le hold_pct de l'autre page
        hold.append(arrondi((tr_b[-1][4] / ouverture) * (1 - frais) ** 2 - 1, 5))
        eng.append(arrondi(max(x[2] / budget for x in tr_dep), 5))
    return {"equity": equity, "hold": hold, "engage_max": eng}


def _perf(b: list[tuple], s: str, rg: Reglages, budget: float) -> float:
    return resume(rejouer(s, b, rg, budget, trace=False))["perf_pct"]


def epreuves(brut: dict[str, list[tuple]], frais: float, budget: float) -> dict:
    """Les trois epreuves calculables sur une configuration unique.

    Les seuils sont ceux de la page des simulations, ecrits d'avance et non
    revus apres coup — c'est la seule chose qui leur donne une valeur.
    """
    faire = lambda **kw: Reglages(frais=frais, **{**REGLAGE, **kw})  # noqa: E731
    base = faire()
    par_paire = {s: _perf(b, s, base, budget) for s, b in brut.items()}

    #  VOISINAGE : le reglage tient-il quand on le pousse un peu de cote ?
    vois = []
    for cle, facteurs in VOISINAGE.items():
        for f in facteurs:
            try:
                v = faire(**{cle: round(REGLAGE[cle] * f, 5)})
            except GrilleError:
                vois.append({"parametre": cle, "facteur": f, "ecarte": "reglage impossible"})
                continue
            if v.echelle(1.0, budget)[0][1] < PLANCHER:
                #  Un premier barreau sous le plancher ne serait jamais passe par
                #  une plateforme reelle : le voisin n'existe pas, on le dit.
                vois.append({"parametre": cle, "facteur": f,
                             "ecarte": "premier barreau sous le plancher"})
                continue
            m = sum(_perf(b, s, v, budget) for s, b in brut.items()) / len(brut)
            vois.append({"parametre": cle, "facteur": f, "perf": arrondi(m, 6)})
    reels = [x["perf"] for x in vois if "perf" in x]
    pire_voisin = min(reels) if reels else None

    #  TRIMESTRES : la fenetre coupee en quatre. ATTENTION, ces quatre trimestres
    #  sont exactement les blocs b5 a b8 de l'etude a neuf cents jours : ce n'est
    #  pas un controle independant, c'est la meme mesure autrement decoupee.
    tri = []
    for k in range(4):
        tranche = {s: b[k * len(b) // 4:(k + 1) * len(b) // 4] for s, b in brut.items()}
        tri.append(arrondi(sum(_perf(b, s, base, budget)
                               for s, b in tranche.items()) / len(tranche), 6))

    passe = {
        "voisinage": bool(reels) and pire_voisin > 0,
        "paires": min(par_paire.values()) > 0,
        "trimestres": sum(1 for x in tri if x > 0) >= 3,
    }
    return {
        "voisinage": {"detail": vois, "pire": arrondi(pire_voisin, 6) if reels else None},
        "paires": {"pire": min(par_paire, key=par_paire.get),
                   "pire_perf": arrondi(min(par_paire.values()), 6),
                   "positives": sum(1 for v in par_paire.values() if v > 0),
                   "sur": len(par_paire)},
        "trimestres": {"perfs": tri, "positifs": sum(1 for x in tri if x > 0)},
        "resolutions": None,     # indisponible : voir "resolutions_motif"
        "passe": passe,
        "reussies": sum(passe.values()),
        "calculables": len(passe),
    }


def comparer_aux_blocs(par_paire: dict) -> dict:
    """L'ecart entre les deux protocoles, chiffre plutot que raconte.

    L'etude a neuf cents jours remet l'echelle a neuf tous les cent jours. Ses
    blocs b5 a b8 couvrent la meme periode que cette mesure-ci, a cinq minutes
    pres. Les composer donne ce que la meme donnee rend AVEC les remises a neuf,
    et la difference est l'effet du protocole, rien d'autre.
    """
    f = RACINE / "docs/etude-dix-paires.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    out = {}
    for s, blocs in d["resultats"].items():
        derniers = [b for b in blocs[5:9] if b]
        if len(derniers) != 4:
            continue          # une paire incomplete n'est pas comparable
        compose = 1.0
        for b in derniers:
            compose *= 1 + b["perf"]
        out[s] = {"compose": arrondi(compose - 1, 6),
                  "continu": arrondi(par_paire[s]["perf"], 6) if s in par_paire else None,
                  "engage_blocs": arrondi(sum(b["engage"] for b in derniers) / 4, 5)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trading.db")
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--frais", type=float, default=0.001)
    ap.add_argument("--sortie", default="docs/data/paires.json")
    args = ap.parse_args()

    verifier_reglage()
    t0, n = fenetre()
    paires = paires_du_panier()
    print(f"  fenetre heritee de la page : t0={t0}, {n} h = {n / 24:.0f} jours")
    print(f"  {len(paires)} paires : {', '.join(p.split('/')[0] for p in paires)}")

    st = Storage(args.db, None)
    brut = {s: serie(st, s, t0, n) for s in paires}
    rg = Reglages(frais=args.frais, **REGLAGE)
    ech = rg.echelle(1.0, 1.0)

    par_paire = {}
    for s, b in brut.items():
        r = rejouer(s, b, rg, args.budget)
        res = resume(r)
        par_paire[s] = {
            "resume": {k: arrondi(v, 6) if isinstance(v, float) else v
                       for k, v in res.items()},
            "perf": res["perf_pct"],
            "hold": arrondi((b[-1][4] / b[0][1]) * (1 - args.frais) ** 2 - 1, 5),
            "jours": journalier(r, b, args.budget, args.frais),
            "barreau_le_plus_bas": max((e.palier for e in r["journal"]
                                        if e.genre == "achat"), default=-1),
        }
        p = par_paire[s]
        print(f"    {s:<10} {res['perf_pct']:+7.2%}  ne rien faire {p['hold']:+7.2%}"
              f"  {res['cycles']:>3} cycles  engage {res['engage_moyen']:.1%}")

    ep = epreuves(brut, args.frais, args.budget)
    print(f"  epreuves calculables : {ep['reussies']}/{ep['calculables']} "
          f"(voisinage {'ok' if ep['passe']['voisinage'] else 'ECHEC'}, "
          f"paires {'ok' if ep['passe']['paires'] else 'ECHEC'}, "
          f"trimestres {'ok' if ep['passe']['trimestres'] else 'ECHEC'})")

    #  La quatrieme epreuve, et POURQUOI elle manque : un compte de paires, pas
    #  un haussement d'epaules.
    sans = [s for s in paires
            if not st._conn.execute(
                "SELECT 1 FROM candles WHERE symbol=? AND timeframe='1h' LIMIT 1",
                (s,)).fetchone()]
    ep["resolutions_motif"] = (
        f"indisponible : {len(sans)} paires sur {len(paires)} n'ont ni bougie horaire "
        f"ni bougie a la minute dans la base ({', '.join(x.split('/')[0] for x in sans)})")
    print(f"  {ep['resolutions_motif']}")

    sortie = RACINE / args.sortie
    sortie.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "meta": {
            "t0": t0, "heures": n, "jours": n // 24, "budget": args.budget,
            "frais": args.frais, "pas_simulation": PAS_SIM, "plancher": PLANCHER,
            "paires": paires, "reglage": REGLAGE,
            "prix_pct": [arrondi(p, 6) for p, _ in ech],
            "mises_pct": [arrondi(e, 8) for _, e in ech],
            "liquidite": json.loads(
                (RACINE / "docs/archives/liquidite.json").read_text(encoding="utf-8")),
            #  Le cout du surajustement est RELU dans la validation, jamais recopie :
            #  une page qui cite un chiffre mesure ailleurs doit le citer a jour.
            "surajustement": json.loads(
                (RACINE / "docs/data/validation.json").read_text(encoding="utf-8")
            )["verdict"]["surajustement"],
        },
        "paires": par_paire,
        "epreuves": ep,
        "protocole": comparer_aux_blocs(par_paire),
    }
    sortie.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                      encoding="utf-8")
    print(f"  ecrit dans {args.sortie} ({sortie.stat().st_size / 1024:.0f} ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
