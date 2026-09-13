"""Branche gabarit.css, prefs.js et reglages.js sur toutes les pages.

    python scripts/poser_reglages.py [--verifier]

TROIS FICHIERS, ET UN ORDRE QUI COMPTE.

gabarit.css vient APRES jetons.css : il pose l'espacement, les rayons, les
tailles et les durees, et il lit les couleurs. Les deux se chargent avant le
<style> de la page, qui garde donc le dernier mot.

prefs.js se charge SANS defer, dans <head>. C'est la seule facon d'eviter le
clignotement : nav.js est en defer, donc un theme applique depuis nav.js
arriverait apres le premier rendu et la page passerait par le clair a chaque
chargement avant de virer au sombre.

reglages.js vient apres nav.js, en defer comme lui : defer garantit l'ordre, et
reglages.js pose ses boutons dans une barre que nav.js a deja construite. Il
ecoute aussi l'evenement « barre-posee », au cas ou l'ordre s'inverserait.

Le script refuse une page ou il ne retrouve pas ses deux ancres, plutot que de
deviner ou poser.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PAGES = ["docs/index.html", "docs/methode.html", "docs/paper.html",
         "docs/journal.html", "docs/validation.html",
         "docs/simulations.template.html", "docs/simulations.html"]

ANCRE_CSS = '<link rel="stylesheet" href="jetons.css">'
SUITE_CSS = ('<link rel="stylesheet" href="jetons.css">\n'
             '<link rel="stylesheet" href="gabarit.css">\n'
             '<script src="prefs.js"></script>')

ANCRE_JS = '<script src="nav.js" defer></script>'
SUITE_JS = ('<script src="nav.js" defer></script>\n'
            '<script src="reglages.js" defer></script>')


def traiter(chemin: Path, verifier: bool) -> str:
    s = chemin.read_text(encoding="utf-8")
    fait_css = "gabarit.css" in s
    fait_js = "reglages.js" in s
    if fait_css and fait_js:
        return "deja pose"
    manques = []
    if not fait_css and ANCRE_CSS not in s:
        manques.append("jetons.css")
    if not fait_js and ANCRE_JS not in s:
        manques.append("nav.js")
    if manques:
        return "ancre introuvable : " + ", ".join(manques)
    if verifier:
        return "a poser"
    if not fait_css:
        s = s.replace(ANCRE_CSS, SUITE_CSS, 1)
    if not fait_js:
        s = s.replace(ANCRE_JS, SUITE_JS, 1)
    chemin.write_text(s, encoding="utf-8")
    return "pose"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    a = ap.parse_args()
    souci = 0
    for p in PAGES:
        c = RACINE / p
        if not c.exists():
            print(f"  {p:<34} absent")
            continue
        etat = traiter(c, a.verifier)
        if "introuvable" in etat:
            souci += 1
        print(f"  {p:<34} {etat}")
    return 1 if souci else 0


if __name__ == "__main__":
    raise SystemExit(main())
