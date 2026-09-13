/*  La barre de navigation, posee en tete de chaque page.
 *
 *  Une seule liste de pages, ici. Ajouter une page se fait a UN endroit, et
 *  toutes les barres l'apprennent — c'est la raison d'etre du fichier : une
 *  liste recopiee dans quatre pages finit toujours par diverger.
 *
 *  Usage, avant la fermeture du corps de page :
 *      <link rel="stylesheet" href="nav.css">
 *      <script src="nav.js"></script>
 *  La page courante est reconnue a son nom de fichier ; rien a declarer.
 */
(function () {
  "use strict";

  const PAGES = [
    { f: "index.html",       nom: "Accueil",           court: "Accueil" },
    { f: "methode.html",     nom: "La methode",        court: "La methode" },
    { f: "paper.html",       nom: "Grilles en direct", court: "En direct" },
    { f: "simulations.html", nom: "Simulations",       court: "Simulations" },
    { f: "journal.html",     nom: "Journal de bord",   court: "Journal" },
  ];

  function fichier() {
    const p = location.pathname.replace(/\/+$/, "");
    const f = p.slice(p.lastIndexOf("/") + 1);
    return f === "" ? "index.html" : f;
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
      a.textContent = p.court;
      if (p.f === ici) a.setAttribute("aria-current", "page");
      nav.appendChild(a);
    }

    const d = document.createElement("a");
    d.className = "pousse";
    d.href = "https://github.com/thomasMareel/claude-trading";
    d.rel = "noopener";
    d.textContent = "Code source";
    nav.appendChild(d);

    document.body.insertBefore(nav, document.body.firstChild);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", poser);
  else poser();
})();
