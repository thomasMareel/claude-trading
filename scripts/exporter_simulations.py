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
from src.grille import Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

HEURE = 3_600_000
GENRE = {"achat": 0, "vente": 1, "abandon": 2}

#  Le plancher qui compte : la couche de risque reelle refuse tout ordre sous
#  max(risk.min_order_value, minimum de la plateforme). Avec une progression
#  geometrique forte, la moitie haute de l'echelle passe sous ce plancher et
#  n'aurait jamais ete posee. Rejouer sans ce plancher compte des operations de
#  deux centimes comme des trades : c'est la difference entre la strategie sur
#  le papier et la strategie executable.
PLANCHER = 12.0
PLANCHERS_COMPARES = (0.0, 5.0)

#  Les huit reglages compares. Le dernier est celui que l'utilisateur appliquait
#  a la main sur son tableur : il sert de point de depart, pas de repoussoir.
REGLAGES = [
    ("p20-14-r22-o2", 0.20, 14, 2.2, 0.02, "Prudent profond"),
    ("p12-10-r22-o1", 0.12, 10, 2.2, 0.01, "Prudent rapide"),
    ("p15-14-r22-o3", 0.15, 14, 2.2, 0.03, "Equilibre"),
    ("p20-14-r14-o2", 0.20, 14, 1.4, 0.02, "Mises plus egales"),
    ("p15-14-r14-o3", 0.15, 14, 1.4, 0.03, "Mises egales, moins profond"),
    ("p12-10-r14-o1", 0.12, 10, 1.4, 0.01, "Engage vite, peu profond"),
    ("p20-14-r10-o2", 0.20, 14, 1.0, 0.02, "Mises identiques"),
    ("p08-14-r18-o2", 0.08, 14, 1.8, 0.02, "Le tableau d'origine"),
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


def grille_reguliere(b: list[tuple]) -> tuple[int, int]:
    """Verifie que les bougies forment bien un pas horaire regulier.

    Toute la compacite de l'export repose sur cette hypothese : si elle est
    fausse, les indices d'evenements designent la mauvaise heure et le graphique
    ment sans rien signaler. On refuse plutot que de tracer un mensonge.
    """
    t0 = b[0][0]
    for i, ligne in enumerate(b):
        if ligne[0] != t0 + i * HEURE:
            raise SystemExit(
                f"historique troue a l'indice {i} : attendu {t0 + i * HEURE}, trouve {ligne[0]}.\n"
                f"Relance scripts/fetch_history.py avant d'exporter."
            )
    return t0, len(b)


def segments_engages(dep: list[tuple]) -> list[list[int]]:
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
    return segs


def marches(suivi: list[tuple], champ: int) -> list[list[float]]:
    """Une serie en escalier, reduite a ses marches : [indice, valeur].

    La reference, le revient et la sortie sont constants entre deux evenements.
    Les exporter heure par heure serait repeter neuf mille fois la meme valeur.
    """
    out: list[list[float]] = []
    prec = None
    for i, ligne in enumerate(suivi):
        v = arrondi(ligne[champ])
        if v != prec:
            out.append([i, v])
            prec = v
    return out


def journalier(dep: list[tuple], eq: list[tuple], budget: float) -> dict:
    """Le resume par jour, pour les vues d'ensemble.

    On garde le MAXIMUM d'engagement du jour, jamais la moyenne : une moyenne
    journaliere lisserait justement les pics d'engagement, qui sont le fait le
    plus important a montrer.
    """
    eng_max, eng_moy, equity, abandon = [], [], [], []
    for j in range(0, len(dep), 24):
        tranche = dep[j:j + 24]
        e = [x[2] / budget for x in tranche]
        eng_max.append(arrondi(max(e), 5))
        eng_moy.append(arrondi(sum(e) / len(e), 5))
        equity.append(arrondi(eq[j:j + 24][-1][1], 2))
        abandon.append(1 if any(x[4] for x in tranche) else 0)
    return {"engage_max": eng_max, "engage_moyen": eng_moy, "equity": equity, "abandon": abandon}


def exporter(cfg, st: Storage, budget: float) -> dict:
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    paires: dict[str, dict] = {}
    brut: dict[str, list[tuple]] = {}
    t0 = n = None

    for s in cfg.symbols:
        b = bougies(st, s)
        if len(b) < 100:
            print(f"  {s} ignoree : {len(b)} bougies seulement")
            continue
        d0, dn = grille_reguliere(b)
        if t0 is None:
            t0, n = d0, dn
        elif (d0, dn) != (t0, n):
            raise SystemExit(f"{s} ne couvre pas la meme fenetre que les autres paires")
        brut[s] = b
        ouverture, cloture = b[0][1], b[-1][4]
        paires[s] = {
            # le prix est le decor commun a toutes les simulations : une seule copie
            "close": [arrondi(x[4], 4) for x in b],
            "haut": [arrondi(x[2], 4) for x in b],
            "bas": [arrondi(x[3], 4) for x in b],
            # le repere honnete : acheter au debut, ne rien faire, payer les frais
            "hold_pct": arrondi((cloture / ouverture) * (1 - frais) ** 2 - 1, 5),
        }
    if not paires:
        raise SystemExit("aucun historique exploitable ; lance scripts/fetch_history.py")

    sorties = []
    for cle, prof, npal, ratio, obj, nom in REGLAGES:
        faire = lambda m: Reglages(profondeur=prof, paliers=npal, ratio=ratio,  # noqa: E731
                                   objectif_net=obj, frais=frais, abandon_sous=0.15,
                                   mise_min=m)
        rg = faire(PLANCHER)
        # l'echelle est la meme a toute epoque, exprimee en fraction de la reference
        ech = rg.echelle(1.0, 1.0)
        bloc = {
            "id": cle, "nom": nom, "profondeur": prof, "paliers": npal,
            "ratio": ratio, "objectif": obj, "abandon_sous": rg.abandon_sous,
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
                # [indice horaire, genre, prix, palier, euros, revient, gain]
                "journal": [[e.ts // HEURE - t0 // HEURE, GENRE[e.genre], arrondi(e.prix, 4),
                             e.palier, arrondi(e.euros, 4), arrondi(e.revient, 4),
                             arrondi(e.gain, 4)] for e in r["journal"]],
                "references": marches(r["suivi"], 1),
                "revients": marches(r["suivi"], 2),
                "sorties": marches(r["suivi"], 3),
                "segments": segments_engages(r["deploiement"]),
                "cycles": [[c.ouvert_le // HEURE - t0 // HEURE, c.ferme_le // HEURE - t0 // HEURE,
                            c.paliers, arrondi(c.investi, 2), arrondi(c.gain, 4),
                            arrondi(c.gain_pct, 6)] for c in r["cycles"]],
                "jours": journalier(r["deploiement"], r["equity"], budget),
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

    return {
        "meta": {
            "budget": budget, "frais": frais, "t0": t0, "pas": HEURE, "heures": n,
            "jours": n // 24, "paires": list(paires), "genres": ["achat", "vente", "abandon"],
        },
        "paires": paires,
        "reglages": sorties,
    }


def batir_page(json_texte: str, gabarit: Path, sortie: Path) -> Path:
    """Injecte les donnees dans le gabarit pour obtenir une page autonome.

    Une page qui va chercher son JSON par le reseau ne s'ouvre pas depuis un
    fichier local et ne survit pas a un hebergement qui bloque la requete. Une
    seule page qui se suffit a elle-meme s'ouvre partout.
    """
    modele = gabarit.read_text(encoding="utf-8")
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
