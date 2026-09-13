"""Fait tourner la grille en paper trading, en temps reel, sur le vrai marche.

    python scripts/paper_grille.py --once        # un cycle, pour verifier
    python scripts/paper_grille.py               # boucle continue

AUCUNE LOGIQUE DE DECISION N'EST ECRITE ICI. Le programme telecharge les
bougies closes, les accumule, et rappelle rejouer() sur la serie entiere a
chaque reveil. La position courante est donc, par construction, exactement
celle que le backtest aurait produite sur ces memes prix : il n'existe pas de
seconde implementation qui pourrait diverger de la premiere. Le cout est de
rejouer quelques milliers de bougies par minute, ce qui ne se mesure pas.

CE QUE CE PAPER TRADING APPORTE, et que quatre cents jours de rejeu ne
donnent pas : du HORS-ECHANTILLON. Les reglages ont ete choisis en balayant
une periode passee, puis mesures sur cette meme periode. Tout ce qui suit le
lancement est, lui, inconnu au moment du choix. C'est le seul juge honnete.

Aucune cle d'API n'est utilisee, aucun ordre n'est transmis : seules les
donnees publiques de marche sont lues.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from math import floor, isfinite, log10
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.grille import Reglages, rejouer, resume  # noqa: E402
from src.storage import Storage  # noqa: E402

#  Le reglage retenu, et la raison de chaque choix. Rien ici ne doit changer
#  sans etre dit : un reglage modifie en cours de route rend la mesure nulle.
#
#  Les paires sont choisies sur la LIQUIDITE mesuree, jamais sur leurs
#  resultats passes. Le volume en euros etait connu avant la periode et ne dit
#  rien du rendement a venir ; trier sur la performance reviendrait a choisir
#  apres coup, ce qui vaut vingt a trente points d'illusion. DOGE est donc
#  retenue bien qu'elle ait perdu la moitie de sa valeur : c'est le prix d'une
#  regle honnete.
PAIRES = ["BTC/EUR", "ETH/EUR", "XRP/EUR", "SOL/EUR", "DOGE/EUR"]
BUDGET = 200.0          # 1000 EUR au total, repartis a parts egales
PAS = "5m"              # le pas auquel toute l'etude a ete menee

#  DEUX ECHELLES TOURNENT EN PARALLELE, sur les MEMES paires et le MEME budget.
#  Tout ce qui les separe est la forme de l'echelle : la comparaison est donc
#  lisible, ce qui ne serait plus vrai si l'une d'elles avait aussi un autre
#  panier ou une autre mise. Leurs departs different d'une heure — chacune part
#  a l'instant ou SON reglage a ete fige, jamais avant : reculer le depart de la
#  seconde la ferait juger sur une heure deja connue de qui l'a reglee.
#
#  "3paliers"  L'echelle profonde issue du balayage. Sur le panier choisi par
#              regle, c'est la seule famille restee positive : les echelles
#              courtes finissent immobilisees a cent pour cent. Elle passe les
#              quatre epreuves du banc (voisinage, paires, trimestres, trois
#              finesses de bougies). Contrepartie mesuree : des cycles tres
#              longs, loin des douze heures visees.
#
#  "8paliers"  Celle que vous decrivez : premier barreau plus bas, mises plus
#              petites, beaucoup de barreaux, objectif ramene a 2 % nets.
#              UNE CONTRAINTE LA DEFORME, et il faut la dire : le plancher de
#              12 EUR par ordre (config.yaml, min_order_value) impose, sur un
#              budget de 200 EUR reparti en 8 barreaux, une progression d'au
#              plus 1,20 — au-dela, les premiers barreaux tomberaient sous le
#              plancher et ne seraient jamais poses. Une vraie progression
#              geometrique a 8 barreaux (raison 1,6) demanderait 840 EUR sur la
#              SEULE paire. A 200 EUR par paire, "grosses mises en bas" et
#              "huit barreaux" ne peuvent pas coexister : c'est une mesure, pas
#              un choix. L'echelle ci-dessous est donc la plus progressive que
#              le plancher autorise.
#
#  "8paliers_concentre"
#              La meme methode, mais dans sa FORME FORTE : les mille euros sur
#              une seule paire, ce qui laisse enfin la place a une raison de
#              1,6 — mises de 14 a 384 EUR, les grosses tout en bas. C'est la
#              seule facon d'avoir a la fois huit barreaux et la progression,
#              et elle coute la repartition, qui etait la seule protection
#              gratuite du dispositif. La paire est BTC/EUR, choisie sur la
#              LIQUIDITE connue d'avance et jamais sur ses resultats passes :
#              le tableau de scripts/preflight_concentre.py montre que SOL et
#              UNI auraient mieux rendu, et c'est precisement le genre de choix
#              apres coup qui a deja coute vingt a trente points d'illusion.
#
#              L'ESPACEMENT EN PUISSANCE N'EST PAS UN ORNEMENT. Avec des
#              barreaux equidistants descendant a -53 %, la mesure sur 900 jours
#              donne ceci : le dernier barreau (384 EUR, 38 % du budget) n'est
#              JAMAIS achete, l'avant-dernier presque jamais, et 4,4 % seulement
#              du capital travaille. La "forme forte" n'existait que sur le
#              papier ; ce qui produisait le rendement etait le premier barreau
#              a 14 EUR. Une courbure de 2 resserre les barreaux du haut dans la
#              zone ou le prix vit vraiment (-2, -3, -6, -11 %) et etire ceux du
#              bas (-19, -28, -40, -53 %) sans toucher aux extremites ni aux
#              mises : le capital au travail passe a 22,9 % et la duree moyenne
#              d'un cycle de 184 a 105 heures.
#
#              LE SEUIL DE REANCRAGE, propose par la revue comme remede, a ete
#              mesure et ECARTE : l'augmenter fait BAISSER la part du capital au
#              travail (7,2 % a 0 %, 3,7 % a 2 %, 1,5 % a 8 %) et tue des
#              barreaux de plus. Un ancrage collant reste sous le prix pendant
#              les hausses, donc le premier barreau est touche moins souvent et
#              les barreaux profonds demandent une chute encore plus grande.
#
#              CE QUE LA MESURE DIT DE CE PROFIL, et qu'il faut savoir avant de
#              le regarder vivre : sur 900 jours il rend +5,7 % avec un creux de
#              -18,5 %, quand BTC laisse tranquille rend +11,8 % avec un creux de
#              -53 %. Il perd donc contre l'inaction sur sa propre paire, et ce
#              qu'il achete avec cette perte est la profondeur du trou. Le
#              barreau du bas, place a la pire chute jamais enregistree, ne peut
#              par construction se declencher que dans un evenement encore
#              jamais vu : c'est une assurance, pas un moteur.
_COMMUN = dict(suivre_hausse=True, vente_meme_bougie=False, mise_min=12.0)
CINQ = ["BTC/EUR", "ETH/EUR", "XRP/EUR", "SOL/EUR", "DOGE/EUR"]
#  Les dix paires EUR les plus liquides d'OKX. Le classement est PRODUIT PAR UN
#  SCRIPT, scripts/classer_liquidite.py, et archive dans docs/archives/liquidite.json
#  avec sa fenetre datee : un critere qui ne s'accompagne pas de sa mesure n'est
#  qu'une opinion. Mesure du 2026-09-13 sur la fenetre commune 2026-03-10 ->
#  2026-09-06, mediane du volume quote journalier.
#
#  LA MEDIANE, ET NON LA MOYENNE. Une premiere version moyennait trente jours et
#  faisait entrer TRX/EUR en sixieme position : sa fenetre chevauchait un mois a
#  26 M EUR entre deux mois a 2,3 M, ce qui multipliait sa liquidite par dix. Le
#  rapport max/mediane vaut 509 pour TRX et 474 pour DOGE — ces marches vivent
#  par a-coups, et toute moyenne courte s'y trompe.
#
#  LA FRONTIERE EST ETROITE, il faut le dire : UNI, dixieme, tient 17 k EUR par
#  jour contre 16 k EUR a AVAX, onzieme. Un rapport de 1,1, pas de vingt. La
#  composition du panier depend donc du choix de la fenetre, et c'est une raison
#  de ne jamais presenter cette liste comme une evidence.
#
#  Le critere reste connu d'avance et n'est jamais un rendement : trier sur la
#  performance passee a deja ete mesure a vingt ou trente points d'illusion.
DIX = ["BTC/EUR", "ETH/EUR", "XRP/EUR", "SOL/EUR", "DOGE/EUR",
       "TRX/EUR", "UNI/EUR", "LINK/EUR", "ADA/EUR", "LTC/EUR"]
PROFILS = {
    "3paliers": dict(
        paires=CINQ, budget=200.0,
        reglage=dict(profondeur=0.50, paliers=3, ratio=3.3, objectif_net=0.04,
                     depart_sous=0.02, **_COMMUN)),
    "8paliers": dict(
        paires=CINQ, budget=200.0,
        reglage=dict(profondeur=0.50, paliers=8, ratio=1.20, objectif_net=0.02,
                     depart_sous=0.02, **_COMMUN)),
    "8paliers_concentre": dict(
        paires=DIX, budget=1000.0,
        reglage=dict(profondeur=0.51, paliers=8, ratio=1.6, objectif_net=0.02,
                     depart_sous=0.02, espacement="puissance", courbure=2.0, **_COMMUN)),
}
REGLAGE = PROFILS["3paliers"]["reglage"]   # le defaut, inchange depuis le 8 septembre

#  Ce que la page d'observation affiche pour expliquer chaque bot. Ecrit ici et
#  publie dans le JSON : la page n'a pas a redire ce que le programme sait deja,
#  et une explication qui vit a cote du reglage ne peut pas le contredire.
TITRES = {
    "3paliers": "Trois barreaux, panier de cinq",
    "8paliers": "Huit barreaux, panier de cinq",
    "8paliers_concentre": "Huit barreaux, dix paires",
}
EXPLICATIONS = {
    "3paliers":
        "Trois ordres d'achat sous le prix, à -2 %, -27 % et -52 %, avec des mises "
        "de 13, 43 et 143 EUR : l'essentiel de l'argent est tout en bas. Chaque "
        "palier touché fait baisser le prix de revient moyen beaucoup plus vite que "
        "le marché. Tout le lot est revendu d'un coup dès que le prix repasse 4 % "
        "nets au-dessus de ce prix de revient — le cours n'a donc pas besoin de "
        "revenir à son point de départ. Tant que rien n'est acheté, l'échelle "
        "remonte avec le marché. C'est le réglage issu du balayage : sur 900 jours "
        "il est positif sur les neuf blocs de cent jours, pire bloc +0,2 %.",
    "8paliers":
        "La même idée avec huit ordres étalés de -2 % à -52 %, un objectif ramené à "
        "2 % nets, donc des ventes plus fréquentes. Le plancher de 12 EUR par ordre "
        "impose ici une progression de 1,20 seulement : les mises vont de 12 à 43 "
        "EUR, presque plates. Ce bot engage donc plus d'argent haut dans la "
        "descente que le précédent — il gagne plus souvent en marché calme et prend "
        "un trou plus profond dans une baisse durable.",
    "8paliers_concentre":
        "Dix expériences indépendantes menées en parallèle sous la MÊME échelle, "
        "mille euros chacune, pour isoler ce que la paire change. Ce n'est pas un "
        "portefeuille de dix mille euros : c'est un dispositif de mesure. L'échelle "
        "à huit barreaux avec une vraie progression des mises — 14 à 384 EUR, les "
        "grosses tout en bas — que le plancher de 12 EUR par ordre interdit dès "
        "qu'on répartit un budget plus petit. Les barreaux ne sont pas équidistants "
        ": resserrés en haut (-2, -3, -6, -11 %) et étirés en bas (-19, -28, -40, "
        "-53 %), parce qu'une échelle régulière descendant à -53 % place la moitié "
        "de ses barreaux là où le prix ne va jamais. Sur 900 jours de passé, la "
        "même échelle va de +5,3 % par bloc de cent jours sur UNI à -1,1 % sur ADA "
        ": six points d'écart pour une échelle identique, et c'est précisément ce "
        "que le direct doit confirmer ou non.",
}


def etat_json(chemin: Path, sortie: Path, profil: str,
              paires: list[str], budget: float, reglage: dict) -> dict:
    """Relit l'etat, et REFUSE de perdre un t0 par accident.

    Le fichier d'etat n'est pas suivi par git : un nettoyage, une copie de
    dossier ou une coupure pendant l'ecriture le fait disparaitre ou le tronque.
    Repartir de zero serait alors silencieux et couterait toute la mesure hors
    echantillon, qu'aucun rejeu ne peut reconstituer. On va donc chercher le t0
    dans le journal publie, qui, lui, est suivi par git.
    """
    brut = None
    if chemin.exists():
        try:
            brut = json.loads(chemin.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:      # fichier tronque
            secours = chemin.with_suffix(".corrompu")
            try:
                chemin.replace(secours)
            except OSError:
                pass
            print(f"  etat illisible ({type(e).__name__}), mis de cote dans {secours.name}",
                  flush=True)
    if brut is None:
        brut = {"depuis": None, "vus": {}}
    brut.setdefault("vus", {})
    if brut.get("depuis") is None and sortie.exists():
        try:
            publie = json.loads(sortie.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            publie = None
        if publie and publie.get("depuis"):
            #  ON NE REPREND UN t0 QUE S'IL APPARTIENT A LA MEME CONFIGURATION.
            #  Le nom du profil ne suffit pas : le profil "8paliers_concentre" est
            #  passe d'une paire a dix sans changer de nom, et reprendre son
            #  ancien t0 aurait donne quatre jours d'avance a un panier decide
            #  apres coup — exactement la faute chiffree a vingt ou trente points
            #  dans ce depot, et elle aurait ete silencieuse.
            avant = publie.get("reglage") or {}
            ecarts = []
            if avant:
                if list(avant.get("paires") or []) != list(paires):
                    ecarts.append(f"paires {len(avant.get('paires') or [])} -> {len(paires)}")
                if avant.get("budget") is not None and float(avant["budget"]) != float(budget):
                    ecarts.append(f"budget {avant['budget']} -> {budget}")
                for k, v in (reglage or {}).items():
                    if k in avant and avant[k] != v:
                        ecarts.append(f"{k} {avant[k]} -> {v}")
            if ecarts:
                raise SystemExit(
                    f"{sortie} porte un t0 du "
                    f"{datetime.fromtimestamp(int(publie['depuis'])/1000, timezone.utc):%Y-%m-%d %H:%M} UTC "
                    f"mais une AUTRE configuration ({', '.join(ecarts[:4])}). Reprendre ce "
                    f"t0 donnerait a la nouvelle configuration des jours qu'elle n'a pas "
                    f"vecus. Archive puis supprime {sortie.name} pour repartir a zero.")
            brut["depuis"] = int(publie["depuis"])
            print(f"  t0 retrouve dans {sortie.name} : meme configuration, la mesure "
                  f"reprend la ou elle en etait", flush=True)
    #  Un etat porte le nom de son profil : deux profils qui se partageraient un
    #  fichier melangeraient deux mesures sans que rien ne le signale.
    if brut.get("profil") not in (None, profil):
        raise SystemExit(f"{chemin} appartient au profil '{brut['profil']}', pas a "
                         f"'{profil}'. Refus d'ecraser une mesure en cours.")
    brut["profil"] = profil
    return brut


def ecrire_atomique(chemin: Path, contenu: str) -> None:
    """Ecrit par un fichier temporaire puis un remplacement atomique.

    Une ecriture directe laisse, si le courant saute au mauvais moment, un JSON
    tronque que la relecture ne pardonne pas : le chien de garde relancerait
    alors le programme toutes les trente secondes sur le meme fichier casse.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    #  Le nom du temporaire porte le PID : les trois robots ecrivent les memes
    #  fichiers de prix, et un nom fixe les faisait se marcher dessus — l'un
    #  remplacait le temporaire que l'autre etait en train de renommer, et le
    #  perdant echouait sur un fichier disparu.
    tmp = chemin.with_suffix(f"{chemin.suffix}.{os.getpid()}.tmp")
    try:
        tmp.write_text(contenu, encoding="utf-8")
        os.replace(tmp, chemin)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


#  Le fichier de prix publie pour la page. Un pas plus large a mesure que la
#  fenetre s'allonge : garder cinq minutes indefiniment ferait un fichier de
#  plusieurs megaoctets recharge toutes les deux minutes, pour un detail que
#  l'oeil ne distingue plus au-dela de quelques jours. La page reagrege encore
#  par-dessus pour l'affichage, exactement comme la page des simulations.
PALIERS_PAS = ((7, 300_000), (21, 900_000), (60, 3_600_000), (10 ** 6, 14_400_000))


def arrondi(v: float, chiffres: int = 7) -> float:
    """Arrondit a un nombre de chiffres SIGNIFICATIFS, pas de decimales.

    Un prix de BTC n'a pas besoin de huit decimales et un prix de DOGE en
    demande six : arrondir a un nombre fixe de decimales gaspille des octets sur
    l'un et detruit de l'information sur l'autre. Sept chiffres significatifs
    suffisent partout et allegent le fichier que la page recharge.
    """
    if v == 0 or not isfinite(v):
        return 0.0
    return round(v, max(0, chiffres - 1 - floor(log10(abs(v)))))


def pas_publie(duree_ms: int) -> int:
    jours = duree_ms / 86_400_000
    for limite, pas in PALIERS_PAS:
        if jours <= limite:
            return pas
    return PALIERS_PAS[-1][1]


def t0_commun() -> int | None:
    """Le plus ancien depart parmi les profils lances, ou None si aucun.

    Les trois robots ecrivent le MEME fichier de prix. S'ils partaient chacun de
    leur propre t0, le dernier a ecrire tronquerait la serie des autres ; on
    prend donc le plus ancien, et les trois produisent alors un contenu
    identique, ce qui rend la concurrence sans consequence.
    """
    t0 = []
    for nom in PROFILS:
        suff = "" if nom == "3paliers" else f"_{nom}"
        f = Path(f"docs/data/paper_grille{suff}_etat.json")
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("depuis"):
            t0.append(int(d["depuis"]))
    return min(t0) if t0 else None


def nom_fichier(symbole: str) -> str:
    return symbole.replace("/", "-")


def ecrire_prix(st: Storage, dossier: Path, depuis: int) -> None:
    """Publie les bougies, UN FICHIER PAR PAIRE, plus un index.

    Un seul fichier pour dix paires atteindrait le megaoctet, et la page n'en
    affiche qu'une a la fois : elle telechargerait donc neuf dixiemes de rien.
    Un fichier par paire reste sous cent kilo-octets quelle que soit la duree,
    puisque le pas s'elargit avec la fenetre, et la page ne charge que la serie
    qu'elle montre.
    """
    maintenant = int(time.time() * 1000)
    pas = pas_publie(maintenant - depuis)
    facteur = pas // 300_000
    dossier.mkdir(parents=True, exist_ok=True)
    paires = sorted({p for pr in PROFILS.values() for p in pr["paires"]})

    #  Les trois robots appellent cette fonction chaque minute sur le MEME jeu de
    #  prix : deux tiers du travail sont donc redondants, et c'est le poste le
    #  plus lourd du cycle puisqu'il relit toute la base pour toutes les paires.
    #  On sort tout de suite si l'index publie est deja a jour — meme fenetre,
    #  meme pas, et la derniere bougie connue de la base n'a pas bouge.
    derniere = st._conn.execute(
        "SELECT MAX(ts) m FROM candles WHERE timeframe=? AND symbol IN "
        "(" + ",".join("?" * len(paires)) + ")", (PAS, *paires)).fetchone()["m"]
    idx = dossier / "index.json"
    if idx.exists():
        try:
            deja = json.loads(idx.read_text(encoding="utf-8"))
            if (deja.get("depuis") == depuis and deja.get("pas") == pas
                    and deja.get("derniere") == derniere
                    and set(deja.get("paires") or {}) == set(paires)):
                return
        except (json.JSONDecodeError, OSError):
            pass
    index = {}
    for s in paires:
        rows = st._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND timeframe=? AND ts >= ? ORDER BY ts",
            (s, PAS, depuis)).fetchall()
        serie = []
        for i in range(0, len(rows), facteur):
            lot = rows[i:i + facteur]
            if not lot:
                continue
            #  Une bougie agregee ouvre a la premiere ouverture, ferme a la
            #  derniere cloture, et prend les extremes du groupe : tout autre
            #  raccourci inventerait des meches.
            serie.append([int(lot[0]["ts"]),
                          arrondi(float(lot[0]["open"])),
                          arrondi(max(float(r["high"]) for r in lot)),
                          arrondi(min(float(r["low"]) for r in lot)),
                          arrondi(float(lot[-1]["close"])),
                          round(sum(float(r["volume"] or 0) for r in lot), 2)])
        ecrire_atomique(dossier / f"{nom_fichier(s)}.json", json.dumps({
            "paire": s, "depuis": depuis, "pas": pas,
            "colonnes": ["ts", "o", "h", "l", "c", "v"],
            "bougies": serie}, separators=(",", ":")))
        index[s] = {"fichier": f"{nom_fichier(s)}.json", "bougies": len(serie),
                    "dernier": serie[-1][0] if serie else None}
    ecrire_atomique(idx, json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "depuis": depuis, "pas": pas, "derniere": derniere,
        "paires": index}, separators=(",", ":")))


def bougies_locales(st: Storage, s: str, depuis: int) -> list[tuple]:
    rows = st._conn.execute(
        "SELECT ts, open, high, low, close FROM candles "
        "WHERE symbol=? AND timeframe=? AND ts >= ? ORDER BY ts",
        (s, PAS, depuis)).fetchall()
    return [(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rows]


def un_cycle(cfg, st: Storage, x: Exchange, etat: dict, sortie: Path, verbeux: bool = True,
             reglage: dict | None = None, paires: list[str] | None = None,
             budget: float | None = None) -> dict:
    reglage = reglage or REGLAGE
    paires = paires or PAIRES
    budget = BUDGET if budget is None else budget
    frais = float(cfg.get("exchange.fee_rate", 0.001))
    rg = Reglages(frais=frais, **reglage)
    maintenant = int(time.time() * 1000)

    #  On ne lit que des bougies CLOSES : la derniere en cours mentirait, et
    #  c'est le defaut le plus classique d'un robot qui tourne en direct.
    limite_close = maintenant - (maintenant % 300_000)
    for s in paires:
        deja = st._conn.execute(
            "SELECT MAX(ts) m FROM candles WHERE symbol=? AND timeframe=?", (s, PAS)).fetchone()["m"]
        depuis = int(deja) + 300_000 if deja else etat["depuis"]
        if depuis and depuis < limite_close:
            try:
                lot = x.fetch_ohlcv(s, PAS, limit=300, since=depuis)
                lot = [r for r in lot if r[0] < limite_close]
                if lot:
                    st.upsert_candles(s, PAS, lot)
            except Exception as e:                       # noqa: BLE001
                print(f"  {s} : {type(e).__name__} {str(e)[:70]}", flush=True)

    lignes, total_eq, total_bud = [], 0.0, 0.0
    for s in paires:
        b = bougies_locales(st, s, etat["depuis"])
        if len(b) < 3:
            continue
        r = rejouer(s, b, rg, budget)
        u = resume(r)
        d = r["descente_en_cours"]
        total_eq += r["equity_finale"]
        total_bud += budget
        #  ce qui est nouveau depuis le dernier reveil, pour le journal
        vus = etat["vus"].get(s, 0)
        neufs = [e for e in r["journal"] if e.ts > vus]
        if neufs:
            etat["vus"][s] = max(e.ts for e in neufs)
            for e in neufs:
                q = datetime.fromtimestamp(e.ts / 1000, timezone.utc).strftime("%d/%m %H:%M")
                print(f"  {q}  {s:<9} {e.genre:<8} {e.prix:>12.4f}  {e.euros:>8.2f} EUR"
                      + (f"  gain {e.gain:+.2f}" if e.genre == "vente" else ""), flush=True)
        #  L'echelle telle qu'elle est POSEE en ce moment, barreau par barreau,
        #  avec ce qui est deja achete et ce qui est trop petit pour etre pose :
        #  c'est ce qu'il faut voir pour comprendre d'un coup d'oeil ou en est le
        #  bot, bien plus qu'un pourcentage.
        morts = set(d.barreaux_morts)
        echelle = [{"prix": round(p, 6), "mise": round(e, 2),
                    "rempli": i in d.remplis, "mort": i in morts}
                   for i, (p, e) in enumerate(d.echelle)]
        lignes.append({
            "paire": s, "equity": round(r["equity_finale"], 2),
            "perf": round(u["perf_pct"], 6), "cycles": len(r["cycles"]),
            "engage": round(d.cumul_euros, 2), "barreaux": len(d.remplis),
            "reference": round(d.reference, 6), "prix": b[-1][4],
            "revient": round(d.prix_revient, 6), "sortie": round(d.prix_sortie, 6),
            "abandonnee": d.abandonnee, "bougies": len(b),
            "hold": round(b[-1][4] / b[0][1] * (1 - frais) ** 2 - 1, 6),
            "echelle": echelle,
            "part_engage": round(u["engage_moyen"], 5),
            "duree_moyenne_h": round(u["duree_moyenne_h"], 1),
            "gain_realise": round(u["gain_cumule"], 2),
            "journal": [{"ts": e.ts, "genre": e.genre, "prix": round(e.prix, 6),
                         "euros": round(e.euros, 2),
                         "gain": round(e.gain, 2) if e.genre == "vente" else None}
                        for e in r["journal"][-40:]],
        })

    jours = (max((l["bougies"] for l in lignes), default=0) * 5) / 1440
    etat["dernier"] = maintenant
    resume_ = {
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profil": etat.get("profil"), "titre": TITRES.get(etat.get("profil"), ""),
        "explication": EXPLICATIONS.get(etat.get("profil"), ""),
        "depuis": etat["depuis"], "jours": round(jours, 2),
        "budget_total": total_bud, "equity_total": round(total_eq, 2),
        "perf_total": round(total_eq / total_bud - 1, 6) if total_bud else 0.0,
        "hold_moyen": round(sum(l["hold"] for l in lignes) / len(lignes), 6) if lignes else 0.0,
        "reglage": {**reglage, "budget": budget, "paires": paires, "pas": PAS},
        "paires": lignes,
    }
    ecrire_atomique(sortie, json.dumps(resume_, indent=1))
    #  Les prix, dans un fichier partage par les trois robots : c'est la meme
    #  serie pour tous, la republier trois fois ne coute rien et supprime toute
    #  question de qui doit l'ecrire.
    debut = t0_commun() or etat["depuis"]
    try:
        ecrire_prix(st, Path("docs/data/prix"), debut)
    except Exception as e:                               # noqa: BLE001
        print(f"  prix non publies : {type(e).__name__} {str(e)[:70]}", flush=True)
    if verbeux:
        print(f"  {jours:.2f} j | book {total_eq:.2f} / {total_bud:.0f} EUR "
              f"({resume_['perf_total']:+.2%}) | repere {resume_['hold_moyen']:+.2%}", flush=True)
    return resume_


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--profil", default="3paliers", choices=sorted(PROFILS),
                    help="quelle echelle faire tourner ; chacune a son propre journal")
    ap.add_argument("--intervalle", type=int, default=60, help="secondes entre deux reveils")
    ap.add_argument("--etat", default=None)
    ap.add_argument("--sortie", default=None)
    ap.add_argument("--force", action="store_true",
                    help="passer outre la garde qui detecte une instance deja lancee")
    args = ap.parse_args()
    #  Le profil d'origine garde SES fichiers, au nom inchange : le processus
    #  qui tourne deja depuis le 8 septembre continue d'y ecrire sans rupture.
    suffixe = "" if args.profil == "3paliers" else f"_{args.profil}"
    args.etat = args.etat or f"docs/data/paper_grille{suffixe}_etat.json"
    args.sortie = args.sortie or f"docs/data/paper_grille{suffixe}.json"
    profil = PROFILS[args.profil]
    reglage, paires, budget = profil["reglage"], profil["paires"], profil["budget"]

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    x = Exchange(cfg, trading=False)
    chemin, chemin_sortie = Path(args.etat), Path(args.sortie)
    #  Deux instances du meme profil ecriraient tour a tour le meme journal et
    #  fabriqueraient une mesure incoherente sans rien signaler. Un journal frais
    #  veut dire qu'une boucle tourne deja.
    if chemin_sortie.exists() and not args.force:
        age = time.time() - chemin_sortie.stat().st_mtime
        if age < 3 * args.intervalle:
            raise SystemExit(
                f"{chemin_sortie} a ete ecrit il y a {age:.0f} s : une instance du profil "
                f"'{args.profil}' tourne deja. Arrete-la, ou passe --force si tu es sur.")
    etat = etat_json(chemin, chemin_sortie, args.profil, paires, budget, reglage)
    if etat["depuis"] is None:
        #  t0 : la premiere bougie qui S'OUVRIRA apres le lancement. Arrondir
        #  vers le bas prendrait la bougie deja commencee, dont une partie est
        #  anterieure au reglage : quelques minutes de lookahead, petites mais
        #  gratuites a supprimer.
        etat["depuis"] = (int(time.time() * 1000) // 300_000 + 1) * 300_000
        chemin.parent.mkdir(parents=True, exist_ok=True)
        print(f"Depart du paper trading [{args.profil}] : "
              f"{datetime.fromtimestamp(etat['depuis']/1000, timezone.utc)}")
        print(f"  {len(paires)} paire(s) x {budget:.0f} EUR = {len(paires)*budget:.0f} EUR"
              f"  ({', '.join(paires)})")
        mises = Reglages(frais=0.001, **reglage).echelle(1.0, budget)
        print("  mises, du haut vers le bas : "
              + ", ".join(f"{e:.0f}" for _, e in mises) + " EUR")
        print(f"  echelle : profondeur {reglage['profondeur']:.0%}, {reglage['paliers']} paliers, "
              f"x{reglage['ratio']}, objectif {reglage['objectif_net']:.0%}\n", flush=True)

    while True:
        try:
            un_cycle(cfg, st, x, etat, Path(args.sortie), reglage=reglage,
                     paires=paires, budget=budget)
        except Exception as e:                           # noqa: BLE001
            print(f"cycle en erreur : {type(e).__name__} {str(e)[:120]}", flush=True)
        ecrire_atomique(chemin, json.dumps(etat))
        if args.once:
            break
        time.sleep(args.intervalle)
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
