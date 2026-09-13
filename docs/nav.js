/*  La barre de navigation, posée en tête de chaque page.
 *
 *  Une seule liste de pages, ici. Ajouter une page se fait à UN endroit, et
 *  toutes les barres l'apprennent — c'est la raison d'être du fichier : une
 *  liste recopiée dans quatre pages finit toujours par diverger.
 *
 *  Usage, avant la fermeture du corps de page :
 *      <link rel="stylesheet" href="nav.css">
 *      <script src="nav.js" defer></script>
 *      <script src="reglages.js" defer></script>
 *  La page courante est reconnue à son nom de fichier ; rien à déclarer.
 *  reglages.js vient ensuite poser le thème et l'engrenage au bout de la barre ;
 *  il n'est pas indispensable — sans lui, la navigation fonctionne.
 */
(function () {
  "use strict";

  const PAGES = [
    { f: "index.html",       nom: "Accueil",           court: "Accueil" },
    { f: "methode.html",     nom: "La méthode",        court: "La méthode" },
    { f: "paper.html",       nom: "Grilles en direct", court: "En direct" },
    { f: "simulations.html", nom: "Simulations",       court: "Simulations" },
    { f: "validation.html",  nom: "La validation",     court: "Validation" },
    { f: "journal.html",     nom: "Journal de bord",   court: "Journal" },
  ];

  function fichier() {
    const p = location.pathname.replace(/\/+$/, "");
    const f = p.slice(p.lastIndexOf("/") + 1);
    return f === "" ? "index.html" : f;
  }

  /*  La hauteur réelle de la barre, publiée pour les pages qui ont leur propre
   *  barre d'outils collante. Deux éléments à top:0 se recouvrent : une fois
   *  figée, la seconde disparaissait derrière la première. La valeur est relue
   *  au redimensionnement parce que la barre passe sur deux lignes en étroit.  */
  function publierHauteur(nav) {
    const poser = () => document.documentElement.style
      .setProperty("--h-nav", nav.offsetHeight + "px");
    poser();
    addEventListener("resize", poser, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(poser).observe(nav);
  }

  function poser() {
    if (document.querySelector(".barre-nav")) return;
    const ici = fichier();
    const nav = document.createElement("nav");
    nav.className = "barre-nav";
    nav.setAttribute("aria-label", "Navigation entre les pages");

    if (ici !== "index.html") {
      const a = document.createElement("a");
      a.className = "retour";
      a.href = "index.html";
      a.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" '
        + 'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        + 'stroke-linejoin="round"><path d="M10 3 5 8l5 5"/></svg>Accueil';
      nav.appendChild(a);
      const s = document.createElement("span");
      s.className = "sep";
      nav.appendChild(s);
    } else {
      const e = document.createElement("span");
      e.className = "etiquette";
      e.textContent = "banc d'essai";
      nav.appendChild(e);
    }

    for (const p of PAGES) {
      if (p.f === "index.html") continue;
      const a = document.createElement("a");
      a.href = p.f;
      //  La page courante passe en gras. Sans réserver la place, les six liens
      //  se décalent d'un ou deux pixels selon la page où l'on est : le fantôme
      //  en gras occupe la largeur définitive, invisible.
      a.dataset.nom = p.court;
      a.innerHTML = "";
      a.appendChild(document.createTextNode(p.court));
      if (p.f === ici) a.setAttribute("aria-current", "page");
      nav.appendChild(a);
    }

    const d = document.createElement("a");
    d.className = "pousse source";
    d.href = "https://github.com/thomasMareel/claude-trading";
    d.rel = "noopener";
    d.textContent = "Code source";
    nav.appendChild(d);

    document.body.insertBefore(nav, document.body.firstChild);
    publierHauteur(nav);
    //  reglages.js écoute : il peut arriver avant ou après selon le cache.
    document.dispatchEvent(new CustomEvent("barre-posee", { detail: nav }));
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", poser);
  else poser();
})();
