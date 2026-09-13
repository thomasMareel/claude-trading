"""Applique l'echelle typographique aux pages.

    python scripts/poser_typo.py [--verifier]

TROIS CHANGEMENTS, ET LEURS RAISONS.

1. LE CORPS PASSE EN REM. Cinq pages posaient `font-size:15px` en dur. Un
   lecteur qui a regle son navigateur sur un corps plus grand — parce qu'il voit
   mal, parce que son ecran est loin — etait purement ignore. `var(--corps)`
   vaut 1rem : sa taille par defaut compte enfin, et le site grandit avec elle.
   Effet de bord assume : tout le monde gagne un point de corps, de 15 a 16 px.

2. UN SEUL CORPS D'AFFICHE. Les titres de premier niveau valaient 3,2 rem sur
   l'accueil, 2,9 sur trois pages, 2,5 sur celle du direct — trois decisions
   prises separement, aucune ecrite. Les quatre pages de lecture partagent
   desormais --t-affiche. La page du direct garde le sien : c'est un tableau de
   bord, pas un article, et une affiche de soixante pixels y ecraserait les
   chiffres qui sont le sujet.

3. UNE SECTION COMMENCE PAR UN FILET, avec un segment petrole de quarante
   pixels pose dessus. La hierarchie se voit alors avant meme qu'on ait lu le
   titre, ce qui est exactement ce qu'on demande a une hierarchie. Pas sur le
   journal : ses h2 sont des boutons d'entree qu'on deplie, et un filet les
   ferait passer pour des titres de section.
"""
from __future__ import annotations

import argparse
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

TETES_AV = ('h1,h2,h3{font-family:"Newsreader",Georgia,serif; font-weight:500; '
            'margin:0; text-wrap:balance}')
TETES_AP = ("h1,h2,h3{font-family:var(--f-titre); font-weight:500; margin:0;\n"
            "  text-wrap:balance; font-optical-sizing:auto}")

#  Le filet de section et son segment. Newsreader a des tailles optiques : a
#  cette taille-la, font-optical-sizing:auto va chercher un dessin plus serre,
#  et c'est la seule facon d'en profiter — la police est chargee en variable
#  depuis le debut et ce reglage n'avait jamais ete demande.
FILET = """/*  Une section commence par un filet, et un segment pétrole de quarante pixels
    posé dessus : la hiérarchie se voit avant même qu'on ait lu le titre. */
h2{position:relative; padding-top:var(--e-5); border-top:1px solid var(--rule)}
h2::before{content:""; position:absolute; top:-1px; left:0;
  width:40px; height:2px; background:var(--achat)}
h2:first-of-type{margin-top:var(--e-6)}
@media(max-width:700px){h2{margin-top:var(--e-6)}}
"""

PAGES: dict[str, list[tuple[str, str]]] = {}


def ed(page: str, avant: str, apres: str) -> None:
    PAGES.setdefault(page, []).append((avant, apres))


for f in ("index", "methode", "validation", "journal", "paper"):
    p = f"docs/{f}.html"
    ed(p, "  font-size:15px; line-height:", "  font-size:var(--corps); line-height:")
    ed(p, TETES_AV, TETES_AP)

#  L'affiche, commune aux quatre pages de lecture.
for f, avant in (("index", "h1{font-size:clamp(2rem,6.2vw,3.2rem); line-height:1.08; letter-spacing:-.015em}"),
                 ("methode", "h1{font-size:clamp(1.9rem,5.6vw,2.9rem); line-height:1.1; letter-spacing:-.015em}"),
                 ("validation", "h1{font-size:clamp(1.9rem,5.6vw,2.9rem); line-height:1.1; letter-spacing:-.015em}"),
                 ("journal", "h1{font-size:clamp(1.9rem,5.6vw,2.9rem); line-height:1.1; letter-spacing:-.015em}")):
    ed(f"docs/{f}.html", avant,
       "h1{font-size:var(--t-affiche); line-height:1.03; letter-spacing:-.022em}")

#  Le filet de section, sur les trois pages ou un h2 est un titre de section.
ed("docs/index.html", "h2{font-size:clamp(1.15rem,3vw,1.45rem)}",
   "h2{font-size:var(--t-section); line-height:1.15; letter-spacing:-.012em;\n"
   "  margin:var(--e-8) 0 var(--e-2)}\n" + FILET)
ed("docs/methode.html", "h2{font-size:clamp(1.3rem,3.4vw,1.7rem); line-height:1.2; margin:44px 0 2px}",
   "h2{font-size:var(--t-section); line-height:1.15; letter-spacing:-.012em;\n"
   "  margin:var(--e-8) 0 var(--e-2)}\n" + FILET)
ed("docs/validation.html", "h2{font-size:clamp(1.3rem,3.4vw,1.7rem); margin:46px 0 4px}",
   "h2{font-size:var(--t-section); line-height:1.15; letter-spacing:-.012em;\n"
   "  margin:var(--e-8) 0 var(--e-2)}\n" + FILET)

#  Les sous-titres : font-family:inherit empechait un h3 de se lire comme un titre.
for f, avant, apres in (
    ("methode", "h3{font-size:1.05rem; font-weight:600; font-family:inherit; margin:26px 0 2px}",
     "h3{font-size:var(--t-sous); font-weight:600; line-height:1.3; margin:var(--e-6) 0 2px}"),
    ("validation", "h3{font-size:1.02rem; font-weight:600; font-family:inherit; margin:24px 0 2px}",
     "h3{font-size:var(--t-sous); font-weight:600; line-height:1.3; margin:var(--e-5) 0 2px}"),
    ("paper", "h3{font-size:1rem; font-weight:600; font-family:inherit}",
     "h3{font-size:1rem; font-weight:600; line-height:1.3}"),
    ("index", "h3{font-size:1.12rem; font-weight:500}",
     "h3{font-size:var(--t-sous); font-weight:600; line-height:1.25}"),
):
    ed(f"docs/{f}.html", avant, apres)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args()
    souci = 0
    for page, edits in PAGES.items():
        c = RACINE / page
        if not c.exists():
            print(f"  {page:<26} absent")
            continue
        s = c.read_text(encoding="utf-8")
        poses = deja = manques = 0
        for a, b in edits:
            if b in s:
                deja += 1
            elif a in s:
                s = s.replace(a, b, 1)
                poses += 1
            else:
                manques += 1
        if manques:
            souci += 1
        if poses and not args.verifier:
            c.write_text(s, encoding="utf-8")
        print(f"  {page:<26} {poses} pose(s), {deja} deja, {manques} introuvable(s)")
    return 1 if souci else 0


if __name__ == "__main__":
    raise SystemExit(main())
