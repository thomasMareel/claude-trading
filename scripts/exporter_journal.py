"""Fabrique le journal de bord du site a partir de l'historique git.

    python scripts/exporter_journal.py

POURQUOI CE JOURNAL EXISTE. Des centaines de pages publient des courbes de
backtest. Presque aucune ne publie, datees, les erreurs qu'elle a trouvees dans
ses propres resultats. Ce depot le fait deja — « le texte publie decrivait un
robot qui n'existait plus », « le classement de liquidite que j'ai annonce etait
faux », « une bougie plate perdait sa jambe basse, donc tous ses achats » — mais
uniquement dans des messages de commit que personne ne lit. Le materiau est
ecrit, relu, date : il ne manquait que de le sortir.

CE QUE CE SCRIPT NE FAIT PAS. Il ne reecrit rien, ne resume rien et ne choisit
rien : il prend les messages tels qu'ils sont. Un journal qui trierait ses
entrees ne vaudrait pas mieux qu'une page de resultats triee.

LA PASTILLE « CORRECTION » EST UNE HEURISTIQUE, et la page le dit. Elle repose
sur des mots du titre et du corps ; elle peut manquer une entree ou en marquer
une a tort. Les faux positifs constates sont corriges a la main dans FORCER,
ci-dessous, plutot que par un reglage de l'heuristique que personne ne pourrait
verifier.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from accentuer import accentuer_prose   # noqa: E402

#  ———————————————————————————————————————————————————————————————————
#  LES ACCENTS, POSES A L'EXPORT ET NON DANS GIT.
#
#  Les messages de commit de ce depot sont ecrits sans accents : ils passent par
#  des lignes de commande, des heredocs et des terminaux dont l'encodage n'est
#  pas garanti, et un message a moitie casse ne se rattrape pas. La page, elle,
#  est du HTML en UTF-8 et doit se lire en francais correct — c'etait la seule
#  des six ou tout etait encore ecrit « methode », « reglage », « donnees ».
#
#  Le meme dictionnaire que le reste du site, donc les memes prudences : les
#  mots ambigus n'y sont pas, et « a » ne devient jamais « a » accentue tout
#  seul. Restent a masquer les identifiants que ces messages citent sans arret —
#  selecteurs CSS, appels de fonction, noms de fichiers — sans quoi « .legende
#  span{ } » deviendrait « .legende » avec un accent dans une page qui explique
#  precisement qu'il ne faut pas faire ca.
CODE = [
    re.compile(r"`[^`]*`"),
    re.compile(r"(?<![\w])[.#][A-Za-z][\w-]*"),      # .legende, #bulle
    re.compile(r"\b[A-Za-z_][\w]*\([^)\n]*\)"),   # etat_json(...), sgn()
    re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+"),  # E.calques, Marche.prixCourt
]


def accentuer_entree(t: str) -> str:
    garde: list[str] = []

    def mettre(m):
        garde.append(m.group(0))
        return "%d" % (len(garde) - 1)

    for rx in CODE:
        t = rx.sub(mettre, t)
    t = accentuer_prose(t)
    for _ in range(8):
        neuf = re.sub("(\\d+)", lambda m: garde[int(m.group(1))], t)
        if neuf == t:
            break
        t = neuf
    return t

#  Un separateur que rien, dans un message de commit francais, ne produira.
SEP = "\x1e"
CHAMPS = ["%H", "%h", "%aI", "%s", "%b"]

#  Une entree est marquee « correction » quand son TITRE le dit. Le titre est le
#  resume que l'auteur a lui-meme choisi : s'y fier evite d'etiqueter la moitie
#  du journal, ce que donnait le meme filtre applique au corps entier — trente-
#  deux entrees sur soixante-quatre, autant dire aucune information. « revue »
#  y figure parce que toutes les revues de ce depot ont debouche sur des
#  corrections.
MOTS_TITRE = re.compile(
    r"\b(corrig|corrections?|erreur|defaut|faux|fausse|mentait|bug|"
    r"casse|cassait|invalide|refute|revue|perdait|loupe|"
    r"n'etait pas|n'existe plus|surajustement)", re.I)
#  Sauf quand le corps contient un aveu a la premiere personne que le titre tait.
#  Formules etroites, choisies pour ne pas attraper une simple explication : une
#  seule entree du depot est rattrapee par ce second filet.
AVEU = re.compile(
    r"(erreur de ma part|j'avais|je m'etais|que j'ai (?:publie|annonce|donne)|"
    r"ma propre|correction d'une conclusion|c'est moi qui|j'ai cause)", re.I)

#  Verdicts poses a la main quand l'heuristique se trompe : id court -> etiquette
#  ou None pour n'en poser aucune. Preferer corriger ici que d'ajouter un mot au
#  filtre : une regle ajustee jusqu'a donner le bon resultat ne prouve plus rien.
FORCER: dict[str, str | None] = {}


def lire_git(depuis: Path, limite: int | None) -> list[dict]:
    fmt = SEP.join(CHAMPS) + SEP + "\x1d"
    cmd = ["git", "log", f"--format={fmt}", "--date=iso-strict"]
    if limite:
        cmd.append(f"-{limite}")
    brut = subprocess.run(cmd, cwd=depuis, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if brut.returncode != 0:
        raise SystemExit(f"git log a echoue : {brut.stderr[:200]}")
    out = []
    for bloc in brut.stdout.split("\x1d"):
        bloc = bloc.strip("\n")
        if not bloc.strip():
            continue
        parts = bloc.split(SEP)
        if len(parts) < 5:
            continue
        sha, court, date, titre, corps = parts[0], parts[1], parts[2], parts[3], parts[4]
        #  La ligne d'attribution n'apporte rien au lecteur du site.
        corps = re.sub(r"\n*Co-Authored-By:.*$", "", corps, flags=re.I | re.S).strip()
        out.append(dict(sha=sha, court=court, date=date,
                        titre=accentuer_entree(titre.strip()),
                        corps=accentuer_entree(corps)))
    return out


def fichiers(depuis: Path, sha: str) -> list[str]:
    r = subprocess.run(["git", "show", "--name-only", "--format=", sha],
                       cwd=depuis, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return [l.strip() for l in r.stdout.splitlines() if l.strip()][:40]


def etiqueter(e: dict) -> str | None:
    if e["court"] in FORCER:
        return FORCER[e["court"]]
    if MOTS_TITRE.search(e["titre"]) or AVEU.search(e["corps"]):
        return "correction"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None, help="nombre d'entrees")
    ap.add_argument("--sortie", default="docs/data/journal.json")
    args = ap.parse_args()

    entrees = lire_git(RACINE, args.limite)
    if not entrees:
        raise SystemExit("aucune entree")
    for e in entrees:
        e["fichiers"] = fichiers(RACINE, e["sha"])
        e["etiquette"] = etiqueter(e)
        e.pop("sha")

    n_corr = sum(1 for e in entrees if e["etiquette"] == "correction")
    sortie = Path(args.sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps({
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "depot": "https://github.com/thomasMareel/claude-trading",
        #  Le journal est fabrique AVANT le commit qui le publie : la derniere
        #  entree visible est donc toujours l'avant-derniere du depot. Le dire
        #  plutot que de laisser croire a un retard inexplique.
        "note": "Généré depuis l'historique git avant le commit qui le publie ; "
                "la toute dernière entrée du dépôt y manque donc toujours.",
        "entrees": entrees,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(entrees)} entrees ecrites dans {sortie}")
    print(f"  dont {n_corr} marquees « correction »")
    print(f"  de {entrees[-1]['date'][:10]} a {entrees[0]['date'][:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
