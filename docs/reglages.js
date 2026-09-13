/*  Le thème au bout de la barre, et le panneau de réglages derrière l'engrenage.
 *
 *  CE QUI A LE DROIT D'ENTRER ICI, et le critère tranche seul : deux lecteurs
 *  raisonnables choisiraient-ils différemment, et la différence se voit-elle à
 *  l'écran ? Tout ce qui échoue à ce test est une décision que le site doit
 *  prendre lui-même. Un panneau de réglages qui accepte tout devient le tiroir
 *  où l'on range les arbitrages qu'on n'a pas voulu faire.
 *
 *  SECOND CRITÈRE, propre à un site dont le sujet est la vérifiabilité : un
 *  réglage ne change JAMAIS un chiffre en silence. Le fuseau et le nombre de
 *  décimales modifient ce qu'un nombre veut dire ; ils sont donc écrits en
 *  clair sous les figures qu'ils touchent, faute de quoi deux visiteurs
 *  compareraient des pages différentes en croyant le contraire.
 *
 *  CE QUI A ÉTÉ REFUSÉ, et pourquoi. La taille du texte : le navigateur la fait
 *  déjà, et le vrai correctif était de passer le corps en rem, ce qui est fait.
 *  La largeur de colonne et le choix de la police : la mesure et la hiérarchie
 *  sont des décisions du site. L'échelle logarithmique et la vue en variation :
 *  ce sont des états DU GRAPHIQUE et non des préférences du lecteur — leur place
 *  est sur la barre d'outils et dans l'adresse. Les calques (volume, échelle,
 *  capital engagé) : on s'en sert à chaque lecture, ils restent en jetons
 *  visibles sur la page.
 *
 *  Dix réglages, plafond dur. Au-delà, un nouveau doit en déloger un.
 */
(function (global) {
  "use strict";

  const P = global.Prefs;
  if (!P) return;   //  sans le magasin de préférences, on ne pose rien

  /* ——— icônes, en 16 unités ——— */
  const ICONES = {
    auto: '<path d="M8 1.6a6.4 6.4 0 1 0 0 12.8z" fill="currentColor" stroke="none"/>'
        + '<circle cx="8" cy="8" r="6.4"/>',
    clair: '<circle cx="8" cy="8" r="3.1"/><path d="M8 .9v1.8M8 13.3v1.8M.9 8h1.8M13.3 8h1.8'
        + 'M3 3l1.3 1.3M11.7 11.7 13 13M13 3l-1.3 1.3M4.3 11.7 3 13"/>',
    sombre: '<path d="M13.4 9.6A5.8 5.8 0 0 1 6.4 2.6 5.9 5.9 0 1 0 13.4 9.6Z"/>',
    engrenage: '<circle cx="8" cy="8" r="2.3"/><path d="M12.9 9.6a1.1 1.1 0 0 0 .22 1.21l.04.04'
        + 'a1.33 1.33 0 1 1-1.88 1.88l-.04-.04a1.11 1.11 0 0 0-1.88.79V13.6a1.33 1.33 0 1 1-2.66 0'
        + 'v-.07a1.11 1.11 0 0 0-1.95-.74l-.04.04a1.33 1.33 0 1 1-1.88-1.88l.04-.04a1.11 1.11 0 0 0'
        + '-.79-1.88H2.4a1.33 1.33 0 1 1 0-2.66h.07a1.11 1.11 0 0 0 .74-1.95l-.04-.04a1.33 1.33 0 1 1'
        + ' 1.88-1.88l.04.04a1.11 1.11 0 0 0 1.21.22h.07a1.11 1.11 0 0 0 .67-1.01V2.4a1.33 1.33 0 1 1'
        + ' 2.66 0v.07a1.11 1.11 0 0 0 1.88.79l.04-.04a1.33 1.33 0 1 1 1.88 1.88l-.04.04a1.11 1.11 0'
        + ' 0 0-.22 1.21v.07a1.11 1.11 0 0 0 1.01.67h.1a1.33 1.33 0 1 1 0 2.66h-.07a1.11 1.11 0 0 0'
        + '-1.01.67Z"/>',
  };
  function svg(nom) {
    return '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" '
      + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
      + ICONES[nom] + "</svg>";
  }

  /*  ——— une barre segmentée reliée à une préférence ———
   *  Un groupe de boutons radio et non un <select> : les trois états doivent se
   *  voir en même temps, sinon celui qu'on a quitté devient invisible. Un seul
   *  tabindex à zéro, les flèches circulent dans le groupe — c'est ce que fait
   *  un vrai groupe radio, et un lecteur d'écran l'annonce ainsi.  */
  function segmente(cle, libelle, options) {
    const g = document.createElement("div");
    g.className = "segmente";
    g.setAttribute("role", "radiogroup");
    g.setAttribute("aria-label", libelle);

    const btns = options.map(o => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.val = o.v;
      b.title = o.titre || o.nom;
      if (o.icone) b.innerHTML = svg(o.icone);
      const s = document.createElement("span");
      s.dataset.nom = o.nom;
      const i = document.createElement("i");
      i.textContent = o.nom;
      s.appendChild(i);
      b.appendChild(s);
      b.addEventListener("click", () => { P.set(cle, o.v); refaire(); b.focus(); });
      g.appendChild(b);
      return b;
    });

    function refaire() {
      const v = P.get(cle);
      for (const b of btns) {
        const actif = b.dataset.val === v;
        b.setAttribute("aria-checked", actif ? "true" : "false");
        b.tabIndex = actif ? 0 : -1;
      }
      //  Si la valeur enregistrée ne correspond à aucune option (fichier
      //  bricolé à la main, version plus ancienne), le premier bouton reprend
      //  le focus : un groupe sans tabindex à zéro devient inatteignable.
      if (!btns.some(b => b.tabIndex === 0) && btns[0]) btns[0].tabIndex = 0;
    }

    g.addEventListener("keydown", e => {
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!d) return;
      e.preventDefault();
      const i = btns.findIndex(b => b.tabIndex === 0);
      const n = btns[(i + d + btns.length) % btns.length];
      P.set(cle, n.dataset.val); refaire(); n.focus();
    });

    refaire();
    P.sur(refaire);
    return g;
  }

  /*  Une bascule simple, pour le seul réglage qui n'a que deux états et dont le
   *  libellé est déjà une phrase.  */
  function bascule(cle, vrai, faux, libelle) {
    const l = document.createElement("label");
    l.className = "bascule";
    const c = document.createElement("input");
    c.type = "checkbox";
    c.checked = P.get(cle) === vrai;
    c.addEventListener("change", () => P.set(cle, c.checked ? vrai : faux));
    P.sur(() => { c.checked = P.get(cle) === vrai; });
    l.appendChild(c);
    l.appendChild(document.createTextNode(libelle));
    return l;
  }

  function ligne(libelle, controle) {
    const d = document.createElement("div");
    d.className = "ligne";
    const s = document.createElement("span");
    s.className = "lib";
    s.textContent = libelle;
    d.appendChild(s);
    d.appendChild(controle);
    return d;
  }

  function section(titre, lignes) {
    const s = document.createElement("section");
    const h = document.createElement("p");
    h.className = "oeil";
    h.textContent = titre;
    s.appendChild(h);
    for (const l of lignes) s.appendChild(l);
    return s;
  }

  /* ——— le panneau ——— */
  let panneau = null, bouton = null, ouvreur = null;

  function batirPanneau() {
    const p = document.createElement("div");
    p.className = "panneau";
    p.id = "panneau-reglages";
    p.setAttribute("role", "dialog");
    p.setAttribute("aria-label", "Réglages d'affichage");
    p.hidden = true;

    const t = document.createElement("h2");
    t.textContent = "Réglages d'affichage";
    p.appendChild(t);
    const i = document.createElement("p");
    i.className = "intro";
    i.textContent = "Ils valent pour toutes les pages du banc.";
    p.appendChild(i);

    p.appendChild(section("Lecture", [
      ligne("Thème", segmente("theme", "Thème", [
        { v: "auto", nom: "Système", icone: "auto", titre: "Suivre le système" },
        { v: "light", nom: "Clair", icone: "clair" },
        { v: "dark", nom: "Sombre", icone: "sombre" },
      ])),
      ligne("Densité", segmente("densite", "Densité", [
        { v: "large", nom: "Confortable" },
        { v: "compacte", nom: "Compacte" },
      ])),
      ligne("Mouvement", segmente("mouvement", "Mouvement", [
        { v: "auto", nom: "Système" },
        { v: "reduit", nom: "Réduit" },
      ])),
    ]));

    p.appendChild(section("Chiffres", [
      ligne("Heures", segmente("fuseau", "Fuseau horaire", [
        { v: "utc", nom: "UTC" },
        { v: "local", nom: "Locales", titre: "L'heure de cet appareil" },
      ])),
      ligne("Décimales des prix", segmente("decimales", "Décimales", [
        { v: "deux", nom: "Lisibles", titre: "Deux à six décimales selon le prix" },
        { v: "paire", nom: "Complètes", titre: "La précision réelle de la paire" },
      ])),
    ]));

    const lTaille = ligne("Taille des marques", segmente("taille", "Taille des marques", [
      { v: "discrete", nom: "Discrète" },
      { v: "normale", nom: "Normale" },
      { v: "grande", nom: "Grande" },
    ]));
    lTaille.appendChild(bascule("proportion", "mise", "egale",
      "Taille proportionnelle à la mise"));

    p.appendChild(section("Marques et graphique", [
      ligne("Couleur des hausses et des baisses",
        segmente("marques", "Couleur des directions", [
          { v: "vert-rouge", nom: "Vert et rouge" },
          { v: "bleu-orange", nom: "Bleu et orange",
            titre: "Lisible en deutéranopie et en protanopie" },
        ])),
      ligne("Forme des marques", segmente("forme", "Forme des marques", [
        { v: "triangles", nom: "Triangles" },
        { v: "rond-losange", nom: "Rond et losange" },
        { v: "barres", nom: "Barres" },
      ])),
      lTaille,
      ligne("Hauteur du graphique", segmente("hauteur", "Hauteur du graphique", [
        { v: "compact", nom: "Compact" },
        { v: "normal", nom: "Normal" },
        { v: "grand", nom: "Grand" },
      ])),
      ligne("Réticule", segmente("reticule", "Réticule", [
        { v: "aimante", nom: "Aimanté", titre: "Collé à la clôture de la bougie" },
        { v: "libre", nom: "Libre", titre: "Le prix sous le curseur, interpolé" },
      ])),
    ]));

    const pied = document.createElement("div");
    pied.className = "pied";
    const note = document.createElement("p");
    note.textContent = "Ces réglages restent sur cet appareil ; rien n'est envoyé.";
    pied.appendChild(note);
    const raz = document.createElement("button");
    raz.type = "button";
    raz.className = "raz";
    raz.textContent = "Tout remettre à zéro";
    raz.addEventListener("click", () => { P.raz(); dire("Réglages remis à zéro."); });
    pied.appendChild(raz);
    p.appendChild(pied);

    document.body.appendChild(p);
    return p;
  }

  /*  Une ligne qui s'efface, pas une boîte de dialogue : on vient d'appuyer sur
   *  le bouton, on sait ce qu'on a fait.  */
  function dire(texte) {
    const d = document.createElement("div");
    d.className = "dit";
    d.setAttribute("role", "status");
    d.textContent = texte;
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 1600);
  }

  function placer() {
    if (!panneau || !bouton) return;
    const r = bouton.getBoundingClientRect();
    const large = panneau.offsetWidth || 320;
    const x = Math.max(12, Math.min(innerWidth - large - 12, r.right - large));
    panneau.style.left = x + "px";
    panneau.style.top = (r.bottom + 8) + "px";
  }

  function ouvrir() {
    if (!panneau) panneau = batirPanneau();
    ouvreur = document.activeElement;
    panneau.hidden = false;
    panneau.classList.add("entre");
    bouton.setAttribute("aria-expanded", "true");
    placer();
    const prem = panneau.querySelector('button[tabindex="0"], button');
    if (prem) prem.focus();
    addEventListener("resize", placer, { passive: true });
    addEventListener("scroll", placer, { passive: true });
  }

  function fermer(rendreFocus) {
    if (!panneau || panneau.hidden) return;
    panneau.hidden = true;
    panneau.classList.remove("entre");
    bouton.setAttribute("aria-expanded", "false");
    removeEventListener("resize", placer);
    removeEventListener("scroll", placer);
    if (rendreFocus) (ouvreur && ouvreur.isConnected ? ouvreur : bouton).focus();
  }

  function basculer() {
    if (panneau && !panneau.hidden) fermer(true); else ouvrir();
  }

  /* ——— la pose dans la barre ——— */
  function poser(nav) {
    if (!nav || nav.querySelector(".outils")) return;
    const outils = document.createElement("div");
    outils.className = "outils";

    outils.appendChild(segmente("theme", "Thème", [
      { v: "auto", nom: "Système", icone: "auto", titre: "Thème : suivre le système" },
      { v: "light", nom: "Clair", icone: "clair", titre: "Thème clair" },
      { v: "dark", nom: "Sombre", icone: "sombre", titre: "Thème sombre" },
    ]));

    bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "btn-icone";
    bouton.title = "Réglages d'affichage";
    bouton.setAttribute("aria-label", "Réglages d'affichage");
    bouton.setAttribute("aria-expanded", "false");
    bouton.setAttribute("aria-controls", "panneau-reglages");
    bouton.innerHTML = svg("engrenage");
    bouton.addEventListener("click", basculer);
    outils.appendChild(bouton);

    nav.appendChild(outils);
  }

  addEventListener("keydown", e => {
    if (e.key === "Escape") fermer(true);
  });
  addEventListener("pointerdown", e => {
    if (!panneau || panneau.hidden) return;
    if (panneau.contains(e.target) || bouton.contains(e.target)) return;
    fermer(false);
  });

  const dejaLa = document.querySelector(".barre-nav");
  if (dejaLa) poser(dejaLa);
  else document.addEventListener("barre-posee", e => poser(e.detail));
})(window);
