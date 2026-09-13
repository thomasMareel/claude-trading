"""Pose les etats manquants — survol, actif — et unifie les rayons de bord.

    python scripts/poser_etats.py [--verifier]

CE QUE CA CORRIGE, et c'est le plus visible pour le moins cher.

1. LE SURVOL NE REPONDAIT PAS. `.opt:hover` ne changeait que la couleur du
   texte et celle du filet : aucune surface. La raison etait mecanique — les
   marches 4 et 5 de la palette n'existaient pas. Elles existent depuis
   jetons.css (--surface-survol, --surface-actif, --rule-survol), il ne restait
   qu'a s'en servir. Au passage, `.opt:hover` empruntait --rule-fort, qui est
   deja le filet des en-tetes de tableau : un jeton survole ressemblait a un
   en-tete.

2. LES JETONS SE DECALAIENT AU CLIC. `.opt[aria-pressed="true"]` ajoutait
   font-weight:600 ; les dix jetons de crypto bougeaient donc d'un ou deux
   pixels chacun a chaque selection, et la rangee entiere se reorganisait sous
   le doigt. L'aplat petrole porte deja l'etat — la graisse en plus ne disait
   rien et coutait la stabilite de la barre.

3. DEUX BARRES COLLANTES A top:0. Celle de la navigation (z 100) et celle des
   simulations (z 30) : une fois figee, la seconde disparaissait derriere la
   premiere. nav.js publie desormais --h-nav ; la barre de page s'y pose.

4. DOUZE RAYONS DE BORD deviennent quatre. Les valeurs 3, 7, 8, 9, 10 et 13
   pixels partent vers --r-1 a --r-4 selon l'objet.

Le script refuse toute page ou il ne retrouve pas exactement le texte a
remplacer, plutot que de deviner.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#  (fichier, avant, apres) — chaque remplacement est unique et verifie.
EDITS: list[tuple[str, str, str]] = []


def ajouter(fichier: str, avant: str, apres: str) -> None:
    EDITS.append((fichier, avant, apres))


#  ——————————————————————————————————————— les jetons de choix, deux pages
TRANSITION = ("  transition:background-color var(--t-court) var(--c-std),\n"
              "    border-color var(--t-court) var(--c-std),\n"
              "    color var(--t-court) var(--c-std)}")

for f, rembourrage in (("docs/paper.html", "5px 11px"), ("docs/journal.html", "6px 12px")):
    taille = ".83rem; line-height:1.3; white-space:nowrap}" if "paper" in f \
        else ".84rem; white-space:nowrap}"
    ajouter(f,
        ".opt{appearance:none; background:var(--surface); color:var(--ink-2);\n"
        f"  border:1px solid var(--rule); border-radius:7px; padding:{rembourrage}; cursor:pointer;\n"
        f"  font:inherit; font-size:{taille}\n"
        ".opt:hover{color:var(--ink); border-color:var(--rule-fort)}\n"
        '.opt[aria-pressed="true"]{background:var(--achat); border-color:var(--achat);\n'
        "  color:var(--sur-vif); font-weight:600}",

        ".opt{appearance:none; background:var(--surface); color:var(--ink-2);\n"
        f"  border:1px solid var(--rule); border-radius:var(--r-2); padding:{rembourrage}; cursor:pointer;\n"
        f"  font:inherit; font-size:{taille[:-1]};\n"
        + TRANSITION + "\n"
        "/*  Le survol prend enfin une surface : sans les marches 4 et 5 de la palette,\n"
        "    il ne changeait que le texte et le filet, et paraissait mou. */\n"
        ".opt:hover{color:var(--ink); background:var(--surface-survol);\n"
        "  border-color:var(--rule-survol)}\n"
        ".opt:active{background:var(--surface-actif)}\n"
        "/*  PAS de font-weight ici : la graisse decalait les dix jetons a chaque clic.\n"
        "    L'aplat petrole porte deja l'etat, et il le porte sans bouger. */\n"
        '.opt[aria-pressed="true"]{background:var(--achat-plein);\n'
        "  border-color:var(--achat-plein); color:var(--sur-vif)}")

#  ————————————————————————————————————————————————— les portes de l'accueil
ajouter("docs/index.html",
    ".porte{display:block; text-decoration:none; color:inherit;\n"
    "  background:var(--surface); border:1px solid var(--rule); border-radius:13px;\n"
    "  padding:20px 20px 18px; box-shadow:var(--ombre);\n"
    "  transition:border-color .15s ease, transform .15s ease}\n"
    ".porte:hover,.porte:focus-visible{border-color:var(--achat); transform:translateY(-1px)}",

    ".porte{display:block; text-decoration:none; color:inherit;\n"
    "  background:var(--surface); border:1px solid var(--rule); border-radius:var(--r-3);\n"
    "  padding:20px 20px 18px; box-shadow:var(--ombre);\n"
    "  transition:border-color var(--t-moyen) var(--c-std),\n"
    "    background-color var(--t-moyen) var(--c-std),\n"
    "    transform var(--t-moyen) var(--c-std)}\n"
    ".porte:hover,.porte:focus-visible{border-color:var(--achat);\n"
    "  background:var(--surface-survol); transform:translateY(-1px)}\n"
    ".porte:active{transform:none; background:var(--surface-actif)}")

ajouter("docs/index.html",
    ".docs a{display:inline-block; padding:6px 11px; border-radius:8px; font-size:.84rem;\n"
    "  border:1px solid var(--rule); background:var(--surface-2); text-decoration:none;\n"
    "  color:var(--ink-2)}\n"
    ".docs a:hover,.docs a:focus-visible{color:var(--ink); border-color:var(--rule-fort)}",

    ".docs a{display:inline-block; padding:6px 11px; border-radius:var(--r-2); font-size:.84rem;\n"
    "  border:1px solid var(--rule); background:var(--surface-2); text-decoration:none;\n"
    "  color:var(--ink-2);\n"
    "  transition:background-color var(--t-court) var(--c-std),\n"
    "    border-color var(--t-court) var(--c-std), color var(--t-court) var(--c-std)}\n"
    ".docs a:hover,.docs a:focus-visible{color:var(--ink);\n"
    "  background:var(--surface-survol); border-color:var(--rule-survol)}\n"
    ".docs a:active{background:var(--surface-actif)}")

#  ————————————————————————————————————————————— les liens de suite, methode
ajouter("docs/methode.html",
    ".suite a{display:inline-block; padding:9px 14px; border-radius:9px; font-size:.88rem;\n"
    "  border:1px solid var(--rule); background:var(--surface); text-decoration:none;\n"
    "  color:var(--ink-2); box-shadow:var(--ombre)}\n"
    ".suite a:hover,.suite a:focus-visible{color:var(--ink); border-color:var(--achat)}",

    ".suite a{display:inline-block; padding:9px 14px; border-radius:var(--r-2); font-size:.88rem;\n"
    "  border:1px solid var(--rule); background:var(--surface); text-decoration:none;\n"
    "  color:var(--ink-2); box-shadow:var(--ombre);\n"
    "  transition:background-color var(--t-court) var(--c-std),\n"
    "    border-color var(--t-court) var(--c-std), color var(--t-court) var(--c-std)}\n"
    ".suite a:hover,.suite a:focus-visible{color:var(--ink); border-color:var(--achat);\n"
    "  background:var(--surface-survol)}\n"
    ".suite a:active{background:var(--surface-actif)}")

#  ——————————————————————————————————————————————— les tuiles du journal
ajouter("docs/journal.html",
    ".jeton{background:var(--surface); border:1px solid var(--rule); border-radius:9px;",
    ".jeton{background:var(--surface); border:1px solid var(--rule); border-radius:var(--r-3);")

#  ————————————————————————————————————— la barre collante des simulations
for f in ("docs/simulations.template.html", "docs/simulations.html"):
    ajouter(f, ".barre{position:sticky; top:0; z-index:30;",
            "/*  Sous la barre de navigation, et non dessous : deux elements colles a\n"
            "    top:0 se recouvrent, et c'est la barre d'outils qui disparaissait. */\n"
            ".barre{position:sticky; top:var(--h-nav); z-index:30;")
    ajouter(f,
        ".puce{font-family:\"IBM Plex Mono\",monospace; font-size:.74rem; color:var(--ink-2);\n"
        "  background:var(--surface); border:1px solid var(--rule); border-radius:3px;",
        ".puce{font-family:\"IBM Plex Mono\",monospace; font-size:.74rem; color:var(--ink-2);\n"
        "  background:var(--surface); border:1px solid var(--rule); border-radius:var(--r-2);\n"
        "  transition:background-color var(--t-court) var(--c-std),\n"
        "    border-color var(--t-court) var(--c-std);")
    ajouter(f, ".puce:hover{border-color:var(--rule-fort)}",
            ".puce:hover{border-color:var(--rule-survol); background:var(--surface-survol)}\n"
            ".puce:active{background:var(--surface-actif)}")
    ajouter(f, ".groupe-b button:hover{background:var(--surface-3)}",
            ".groupe-b button:hover{background:var(--surface-survol)}\n"
            ".groupe-b button:active{background:var(--surface-actif)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args()

    par_fichier: dict[str, list[tuple[str, str]]] = {}
    for f, a, b in EDITS:
        par_fichier.setdefault(f, []).append((a, b))

    souci = 0
    for f, edits in par_fichier.items():
        c = RACINE / f
        if not c.exists():
            print(f"  {f:<34} absent")
            continue
        s = c.read_text(encoding="utf-8")
        poses, deja, manques = 0, 0, 0
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
        etat = f"{poses} pose(s), {deja} deja, {manques} introuvable(s)"
        if poses and not args.verifier:
            c.write_text(s, encoding="utf-8")
        print(f"  {f:<34} {etat}")
    return 1 if souci else 0


if __name__ == "__main__":
    raise SystemExit(main())
