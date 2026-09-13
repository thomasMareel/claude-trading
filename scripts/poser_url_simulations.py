"""Met l'etat des selecteurs dans l'adresse, sur la page des simulations.

    python scripts/poser_url_simulations.py

Ce que le lecteur vient de trouver — ce reglage, cette paire, cette periode —
doit pouvoir s'envoyer a quelqu'un ou se retrouver demain. Aujourd'hui l'adresse
ne dit rien : on tombe toujours sur le premier reglage, la premiere paire, et la
fenetre entiere.

Le meme correctif est applique a la TEMPLATE et a la page deja construite, pour
qu'un nouvel export ne le perde pas et qu'on n'ait pas a rejouer quatre cents
jours de bougies pour une ligne de JavaScript.
"""
from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CIBLES = ["docs/simulations.template.html", "docs/simulations.html"]

ANCRE = """selR.addEventListener("change", () => { iReg = +selR.value; cycleChoisi = null; rendu(); });"""

REMPLACEMENT = """selR.addEventListener("change", () => {
  iReg = +selR.value; cycleChoisi = null; ecrireUrl(); rendu();
});

/*  L'ETAT DANS L'ADRESSE. replaceState et non pushState : le bouton Retour du
 *  navigateur ne doit pas remonter un a un tous les reglages essayes. La paire
 *  s'ecrit sans son suffixe, la fenetre en indices horaires depuis le debut.  */
function ecrireUrl() {
  const q = new URLSearchParams();
  if (iReg) q.set("reglage", D.reglages[iReg].id || String(iReg));
  if (M.paire !== D.meta.paires[0]) q.set("paire", M.paire.split("/")[0]);
  if (M.h0 > 0 || M.h1 < NH - 1) q.set("vue", M.h0 + "-" + M.h1);
  if (M.uniteManuelle) q.set("bougie", String(M.unite));
  const eteints = Object.entries(M.calques).filter(([, v]) => !v).map(([k]) => k);
  if (eteints.length) q.set("sans", eteints.join(","));
  const f = q.toString();
  history.replaceState(null, "", f ? "#" + f : location.pathname + location.search);
}
function lireUrl() {
  const q = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  const r = q.get("reglage");
  if (r) {
    const k = D.reglages.findIndex(x => x.id === r);
    const n = /^\\d+$/.test(r) ? +r : -1;
    if (k >= 0) iReg = k;
    else if (n >= 0 && n < D.reglages.length) iReg = n;
    selR.value = String(iReg);
  }
  const p = q.get("paire");
  if (p) {
    const s = D.meta.paires.find(x => x.split("/")[0] === p.toUpperCase());
    if (s) M.paire = s;
  }
  const v = (q.get("vue") || "").split("-").map(Number);
  if (v.length === 2 && v.every(Number.isFinite) && v[1] > v[0]) {
    M.h0 = Math.max(0, v[0]); M.h1 = Math.min(NH - 1, v[1]);
  }
  const b = +q.get("bougie");
  if (b > 0) { M.unite = b; M.uniteManuelle = true; }
  const sans = (q.get("sans") || "").split(",").filter(Boolean);
  for (const k of Object.keys(M.calques)) M.calques[k] = !sans.includes(k);
}"""

ANCRE2 = """M.unite = uniteAuto(NH);
majPaires(); majStats();"""

REMPLACEMENT2 = """M.unite = uniteAuto(NH);
lireUrl();
if (!M.uniteManuelle) M.unite = uniteAuto(M.h1 - M.h0);
majPaires(); majStats();"""

ANCRE3 = """brancherGestes(M.toile);
majOutils();
rendu();"""

REMPLACEMENT3 = """brancherGestes(M.toile);
majOutils();
rendu();
ecrireUrl();
//  Un lien colle dans la barre d'adresse doit refaire l'affichage sans recharger.
addEventListener("hashchange", () => {
  lireUrl(); bornerVue(); majPaires(); majStats();
  M.toile.rendre(true); majOutils(); rendu();
});"""

#  Les trois endroits ou un geste change l'etat sans passer par selR.
GESTES = [
    ("""onclick: () => { M.paire = sym; majPaires(); majStats(); M.toile.rendre(true); rendu(); } },""",
     """onclick: () => { M.paire = sym; majPaires(); majStats(); M.toile.rendre(true);
        rendu(); ecrireUrl(); } },"""),
    ("""onclick: () => { M.unite = n; M.uniteManuelle = true; bornerVue(); majOutils(); } }, nom)));""",
     """onclick: () => { M.unite = n; M.uniteManuelle = true; bornerVue(); majOutils();
        ecrireUrl(); } }, nom)));"""),
]


def main() -> int:
    for cible in CIBLES:
        c = RACINE / cible
        if not c.exists():
            print(f"  {cible:<34} absent")
            continue
        s = c.read_text(encoding="utf-8")
        if "function ecrireUrl()" in s:
            print(f"  {cible:<34} deja fait")
            continue
        manques = [n for n, a in (("1", ANCRE), ("2", ANCRE2), ("3", ANCRE3)) if a not in s]
        if manques:
            print(f"  {cible:<34} ancres introuvables : {', '.join(manques)}")
            continue
        s = s.replace(ANCRE, REMPLACEMENT, 1)
        s = s.replace(ANCRE2, REMPLACEMENT2, 1)
        s = s.replace(ANCRE3, REMPLACEMENT3, 1)
        poses = 0
        for av, ap in GESTES:
            if av in s:
                s = s.replace(av, ap, 1)
                poses += 1
        c.write_text(s, encoding="utf-8")
        print(f"  {cible:<34} pose ({poses}/{len(GESTES)} gestes branches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
