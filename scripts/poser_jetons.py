"""Remplace le bloc de jetons recopie dans chaque page par le fichier partage.

    python scripts/poser_jetons.py [--verifier]

Les couleurs vivaient dans cinq fichiers a la fois. Elles avaient deja diverge
sans que personne le voie. Ce script coupe, dans chaque page, la suite des trois
blocs — :root nu, la media-query sombre, [data-theme="dark"] — et met a la place
un lien vers docs/jetons.css. Il refuse de toucher une page dont il ne retrouve
pas exactement ces trois blocs, plutot que de deviner.

--verifier ne modifie rien et dit seulement ou en sont les pages.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PAGES = ["docs/index.html", "docs/methode.html", "docs/paper.html",
         "docs/journal.html", "docs/simulations.template.html", "docs/simulations.html"]
LIEN = '<link rel="stylesheet" href="jetons.css">'

#  Du debut de « :root{ » jusqu'a la fin du bloc [data-theme="dark"]. On borne la
#  recherche par la premiere regle qui suit, toujours la meme dans ces pages.
DEBUT = re.compile(r"(?:/\*[^*]*\*+(?:[^/*][^*]*\*+)*/\s*)?:root\{")
FINS = ("*{box-sizing", "*{ box-sizing", "body{", "html,body{")


def traiter(chemin: Path, verifier: bool) -> str:
    s = chemin.read_text(encoding="utf-8")
    if LIEN in s:
        return "deja pose"
    m = DEBUT.search(s)
    if not m:
        return "pas de bloc :root trouve"
    fin = min((s.find(f, m.end()) for f in FINS if s.find(f, m.end()) > 0), default=-1)
    if fin < 0:
        return "fin du bloc introuvable"
    bloc = s[m.start():fin]
    #  garde-fou : on doit bien avoir les TROIS etats, sinon on ne coupe rien
    if bloc.count(":root") < 3 or "prefers-color-scheme" not in bloc:
        return f"bloc suspect ({bloc.count(':root')} :root), non touche"
    if verifier:
        return f"a remplacer ({len(bloc)} caracteres)"
    #  Le lien doit sortir du <style> : on ferme, on lie, on rouvre.
    s = s[:m.start()] + "</style>\n" + LIEN + "\n<style>\n" + s[fin:]
    chemin.write_text(s, encoding="utf-8")
    return f"remplace ({len(bloc)} caracteres retires)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args()
    for p in PAGES:
        c = RACINE / p
        if not c.exists():
            print(f"  {p:<34} absent")
            continue
        print(f"  {p:<34} {traiter(c, args.verifier)}")
    if not args.verifier:
        print("\nVerifie ensuite qu'aucune page ne definit encore une couleur en dur :")
        print("  grep -n '#[0-9A-Fa-f]\\{6\\}' docs/*.html | grep -v jetons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
