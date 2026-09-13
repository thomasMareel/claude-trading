"""Rend le graphique des simulations obeissant aux reglages, et ses cycles cliquables.

    python scripts/poser_cycle_simulations.py [--verifier]

TROIS CHOSES.

1. LES MARQUES SUIVENT LE REGLAGE. La page dessinait ses triangles elle-meme, en
   dur. Elle emprunte desormais Marche.dessinerMarque — le meme dessin que la
   page « en direct » et que les vignettes de legende — donc la forme et la
   taille choisies dans le panneau s'appliquent ici aussi. Une preference qui ne
   vaut que sur une page sur deux est pire qu'une preference absente : elle
   donne a croire que le site est incoherent.

2. UN CYCLE S'ETUDIE AU CLIC. La planche 1 sait deja tout montrer d'un cycle,
   mais on ne pouvait y arriver que par quatre puces pre-reglees — le plus
   profond, le plus long, le plus rapide, le plus rentable. Celui qu'on voit a
   l'ecran, celui sur lequel on a une question, n'etait atteignable par aucun
   chemin. Un appui bref sur un ordre l'ouvre maintenant dans la planche, qui
   gagne une cinquieme puce : « celui que vous avez choisi ».

3. LE MOT « TRIANGLES » disparait de l'aide : il devient faux des que quelqu'un
   choisit des ronds.

Le script refuse toute page ou il ne retrouve pas exactement le texte a
remplacer, plutot que de deviner ou poser.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CIBLES = ["docs/simulations.template.html", "docs/simulations.html"]

EDITS: list[tuple[str, str]] = []


def ed(avant: str, apres: str) -> None:
    EDITS.append((avant, apres))


#  ——————————————————————————————————— marche.js, pour le dessin et les reglages
ed('<script src="nav.js" defer></script>',
   '<script src="marche.js"></script>\n<script src="nav.js" defer></script>')

#  ——————————————————————————————————————————————— l'aide sous le graphique
ed("Les triangles sont les ordres réellement passés par le réglage sélectionné ci-dessous.",
   "Les marques sont les ordres réellement passés par le réglage sélectionné ci-dessous ; "
   "cliquez-en une pour étudier son cycle.")

#  —————————————————————————————————————————————————— le dessin des marques
ed("""  M.ordres = {};
  if (M.calques.ordres) {""",
   """  M.ordres = {};
  M.marques = [];
  if (M.calques.ordres) {""")

ed("""      if (genre === 0)
        el("path", { d: `M${x} ${y + t}L${x + t} ${y - t * .6}L${x - t} ${y - t * .6}Z`,
          class: "m-achat", stroke: "var(--surface)", "stroke-width": .7 }, go);
      else
        el("path", { d: `M${x} ${y - t}L${x + t} ${y + t * .6}L${x - t} ${y + t * .6}Z`,
          class: "m-vente", stroke: "var(--surface)", "stroke-width": .7 }, go);
      (M.ordres[k] || (M.ordres[k] = [])).push({ genre, prix, pal, euros, gain, i });""",
   """      //  Le meme dessin que sur la page « en direct » et que les vignettes de
      //  legende : la forme et la taille choisies dans le panneau valent ici
      //  aussi. Une preference qui ne s'applique qu'a une page sur deux donne a
      //  croire que le site est incoherent.
      Marche.dessinerMarque(go, x, y, t, genre === 0);
      (M.ordres[k] || (M.ordres[k] = [])).push({ genre, prix, pal, euros, gain, i });
      M.marques.push({ x, y, i });""")

#  ——————————————————————————————————————————— l'appui bref ouvre le cycle
ed("""    if (depart && Math.hypot(e.clientX - depart.x, e.clientY - depart.y) < 7) croixSur(e);
    depart = null; pointeurs.delete(e.pointerId); glisse = null;""",
   """    if (depart && Math.hypot(e.clientX - depart.x, e.clientY - depart.y) < 7) {
      if (!ouvrirCycleSous(e)) croixSur(e);
    }
    depart = null; pointeurs.delete(e.pointerId); glisse = null;""")

ed("""function brancherGestes(toile) {""",
   """/*  Un appui bref sur un ordre ouvre SON cycle dans la planche 1. Les quatre
 *  puces pre-reglees — le plus profond, le plus long, le plus rapide, le plus
 *  rentable — ne menaient pas a celui qu'on a sous les yeux, c'est-a-dire
 *  precisement celui sur lequel on se pose une question.  */
function ouvrirCycleSous(e) {
  if (!M.marques || !M.marques.length || !M.toile) return false;
  const r = M.toile.svg.getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  let best = null, d0 = 14;
  for (const m of M.marques) {
    const d = Math.hypot(m.x - x, m.y - y);
    if (d < d0) { d0 = d; best = m; }
  }
  if (!best) return false;
  const c = S().cycles.find(c => best.i >= c[0] && best.i <= c[1]);
  if (!c) return false;
  cycleChoisi = c;
  rendu();
  const p = document.getElementById("planche-1");
  if (p) p.scrollIntoView({ behavior: "smooth", block: "start" });
  return true;
}

function brancherGestes(toile) {""")

#  ————————————————————————————— la planche accepte un cycle venu du graphique
ed("""  const choix = [];
  const pousse = (c, nom) => { if (c && !choix.some(x => x.c === c)) choix.push({ c, nom }); };
  pousse([...cy].sort((a, b) => b[2] - a[2])[0], "le plus profond");""",
   """  const choix = [];
  const pousse = (c, nom) => { if (c && !choix.some(x => x.c === c)) choix.push({ c, nom }); };
  //  Celui qu'on vient d'ouvrir depuis le graphique passe en tete, et reste
  //  atteignable : sans cette puce, il disparaitrait au premier reclic ailleurs.
  if (cycleChoisi && cy.includes(cycleChoisi)) pousse(cycleChoisi, "celui que vous avez choisi");
  pousse([...cy].sort((a, b) => b[2] - a[2])[0], "le plus profond");""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args()
    souci = 0
    for cible in CIBLES:
        c = RACINE / cible
        if not c.exists():
            print(f"  {cible:<34} absent")
            continue
        s = c.read_text(encoding="utf-8")
        poses = deja = manques = 0
        for a, b in EDITS:
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
        print(f"  {cible:<34} {poses} pose(s), {deja} deja, {manques} introuvable(s)")
    return 1 if souci else 0


if __name__ == "__main__":
    raise SystemExit(main())
