/*  Le graphique de marché, en un seul endroit.
 *
 *  La page des simulations et celle du paper trading montrent la même chose :
 *  des chandeliers, une échelle d'achat posée dessus, des ordres, un réticule
 *  qui lit les prix. Deux implémentations auraient dérivé l'une de l'autre —
 *  ce dépôt a déjà payé cher d'avoir laissé deux vérités coexister. Celle-ci
 *  ne dépend d'aucune variable globale : tout entre par l'objet de création.
 *
 *      const g = new Marche(conteneur, {
 *        bougies : [[ts,o,h,l,c,v], ...],   // au pas le plus fin disponible
 *        pas     : 300000,                   // millisecondes par bougie
 *        ordres  : [{ts, genre:"achat"|"vente", prix, euros, gain}],
 *        niveaux : [{prix, texte, classe, pointille}],   // horizontales : échelle
 *        courbes : [{points:[[ts,prix]], couleur, texte}], // revient, cible de vente
 *        bandes  : [[tsDebut, tsFin]],       // périodes où du capital dort
 *        cycles  : [{t0, t1, ...}],          // pour l'étude d'un cycle au clic
 *        unite   : 12,                       // bougies agrégées par chandelier
 *        nom     : "BTC/EUR",                // porté par la légende fixe
 *        surCycle: (cycle, g) => {},         // appelé à l'entrée et à la sortie
 *      });
 *      g.majDonnees({bougies, ordres, niveaux});   // même objet, autre contenu
 *
 *  Le dessin se fait en PIXELS RÉELS : un viewBox figé étire le texte à des
 *  tailles illisibles dès que le conteneur change de largeur.
 *
 *  TROIS CHOSES QUE CE FICHIER PREND AU SÉRIEUX.
 *
 *  1. Les pixels. Un corps de chandelier posé sur un x fractionnaire a ses deux
 *     bords sur des demi-pixels : le navigateur les étale sur deux colonnes et
 *     la mèche d'un pixel n'est plus centrée sur son corps. Tout ce qui est
 *     vertical passe donc par des entiers + 0,5, et la largeur du corps garde
 *     la même parité que la mèche.
 *
 *  2. Ce qui recouvre ce qu'on lit. L'infobulle flottante décrivait la bougie
 *     sous le curseur en se posant exactement dessus. La lecture OHLC remonte
 *     dans une légende FIXE en haut à gauche, qui ne disparaît jamais ; il ne
 *     reste dans l'infobulle que les ordres, qu'aucune ligne fixe ne peut
 *     porter parce qu'il y en a zéro, un ou trois.
 *
 *  3. Aucune animation sur un chiffre. La vue se déplace en fondu quand on
 *     étudie un cycle, jamais les nombres : un prix illisible pendant deux
 *     dixièmes de seconde est un prix non vérifiable.
 */
(function (global) {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  function el(nom, attrs, parent) {
    const n = document.createElementNS(SVGNS, nom);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  const nb = (v, d) => v.toLocaleString("fr-FR",
    { minimumFractionDigits: d, maximumFractionDigits: d });

  /*  Les préférences du lecteur, avec des valeurs de repli : le graphique doit
   *  fonctionner sur une page qui n'a pas chargé prefs.js.  */
  const REPLI = { forme: "triangles", tailleMarque: 1, proportionnel: true,
                  echelleHauteur: 1, aimante: true, utc: true, decimalesPaire: false };
  function prefs() {
    try {
      return (global.Prefs && global.Prefs.graphique) ? global.Prefs.graphique() : REPLI;
    } catch (e) { return REPLI; }
  }

  function prixCourt(v) {
    if (!isFinite(v)) return "—";
    if (prefs().decimalesPaire)
      //  « Complètes » : la valeur telle qu'elle est stockée, sans arrondi de
      //  confort. C'est le réglage de qui vérifie un ordre ligne à ligne.
      return v.toLocaleString("fr-FR", { maximumFractionDigits: 8 });
    return nb(v, v >= 1000 ? 0 : v >= 10 ? 2 : v >= 0.1 ? 4 : 6);
  }

  const p2 = n => String(n).padStart(2, "0");
  /*  UTC par défaut : c'est l'heure des bougies de l'échange, et deux lecteurs
   *  qui comparent doivent lire la même. Le réglage « heures locales » existe
   *  pour qui relit sa propre journée ; la légende dit alors laquelle.  */
  function parts(ts) {
    const d = new Date(ts);
    return prefs().utc
      ? [d.getUTCDate(), d.getUTCMonth() + 1, d.getUTCHours(), d.getUTCMinutes()]
      : [d.getDate(), d.getMonth() + 1, d.getHours(), d.getMinutes()];
  }
  function jjh(ts) {
    const p = parts(ts);
    return p2(p[0]) + "/" + p2(p[1]) + " " + p2(p[2]) + "h";
  }
  function jjhm(ts) {
    const p = parts(ts);
    return p2(p[0]) + "/" + p2(p[1]) + " " + p2(p[2]) + ":" + p2(p[3]);
  }
  function jj(ts) {
    const p = parts(ts);
    return p2(p[0]) + "/" + p2(p[1]);
  }
  function duree(ms) {
    const h = ms / 3600000;
    if (h < 1) return Math.round(ms / 60000) + " min";
    if (h < 48) return (h < 10 ? h.toFixed(1).replace(".", ",") : Math.round(h)) + " h";
    return Math.round(h / 24) + " j";
  }

  /*  Des graduations que l'œil lit : 1, 2, 2,5 ou 5 fois une puissance de dix.
   *  Un pas « joli » évite les axes gradués à 0,037 qui ne disent rien.  */
  function ticks(lo, hi, combien) {
    if (!(hi > lo)) return [lo];
    const brut = (hi - lo) / Math.max(1, combien);
    const ordre = Math.pow(10, Math.floor(Math.log10(brut)));
    const pas = [1, 2, 2.5, 5, 10].map(m => m * ordre)
      .find(v => v >= brut) || 10 * ordre;
    const out = [];
    for (let v = Math.ceil(lo / pas) * pas; v <= hi; v += pas) out.push(v);
    return out;
  }

  /*  Mesurer un texte sans le poser dans la page. Sert à dimensionner l'axe des
   *  prix : soixante-deux pixels figés découpaient un prix à six chiffres et
   *  gaspillaient un quart de la marge sur une paire à 0,08 €.  */
  let _ctx = null;
  function mesurer(txt, police) {
    if (!_ctx) {
      const c = document.createElement("canvas");
      _ctx = c.getContext && c.getContext("2d");
    }
    if (!_ctx) return String(txt).length * 6.2;   // repli grossier
    _ctx.font = police;
    return _ctx.measureText(txt).width;
  }
  const POLICE_AXE = '400 11px "IBM Plex Mono", ui-monospace, monospace';
  const police = (graisse) => graisse + ' 11.5px "IBM Plex Mono", ui-monospace, monospace';

  function libellePas(ms) {
    if (ms < 3600000) return Math.round(ms / 60000) + " min";
    if (ms < 86400000) return Math.round(ms / 3600000) + " h";
    return Math.round(ms / 86400000) + " j";
  }

  const borne = (v, a, b) => Math.max(a, Math.min(b, v));
  //  Plancher de chandeliers visibles : en dessous, on ne lit plus un marché,
  //  on lit quatre rectangles.
  const MINI = 24;

  /*  La FORME d'une marque porte le sens même sans la couleur — c'est la vraie
   *  réponse au daltonisme, plus sûre qu'un autre couple de teintes. Fonction
   *  libre et non méthode : la légende de la page doit dessiner exactement la
   *  même chose, sinon elle finit par décrire des triangles quand le graphique
   *  montre des ronds.  */
  function dessinerMarque(g, x, y, t, achat) {
    const f = prefs().forme;
    const com = { fill: achat ? "var(--achat)" : "var(--vente)",
              stroke: "var(--surface)", "stroke-width": 0.8 };
    if (f === "rond-losange") {
      if (achat) el("circle", Object.assign({ cx: x, cy: y, r: t * 0.92 }, com), g);
      else el("path", Object.assign({ d: "M" + x + " " + (y - t * 1.1)
        + "L" + (x + t) + " " + y + "L" + x + " " + (y + t * 1.1)
        + "L" + (x - t) + " " + y + "Z" }, com), g);
      return;
    }
    if (f === "barres") {
      //  Une barre posée sur le prix, prolongée d'une tige du côté d'où vient
      //  l'ordre : vers le bas pour un achat, vers le haut pour une vente.
      el("rect", Object.assign({ x: x - t * 1.15, y: y - 1.4,
        width: t * 2.3, height: 2.8, rx: 1 }, com), g);
      el("rect", { x: x - 1, y: achat ? y : y - t * 1.5, width: 2, height: t * 1.5,
        fill: com.fill }, g);
      return;
    }
    el("path", Object.assign({ d: achat
      ? "M" + x + " " + (y + t) + "L" + (x + t) + " " + (y - t * 0.6)
        + "L" + (x - t) + " " + (y - t * 0.6) + "Z"
      : "M" + x + " " + (y - t) + "L" + (x + t) + " " + (y + t * 0.6)
        + "L" + (x - t) + " " + (y + t * 0.6) + "Z" }, com), g);
  }

  class Marche {
    constructor(conteneur, opts) {
      this.el = conteneur;
      this.o = Object.assign({
        bougies: [], pas: 300000, ordres: [], niveaux: [], bandes: [], courbes: [],
        cycles: [], unite: 1, hauteur: 340, volume: true, titreAxe: "", nom: "",
        gestes: true, surCycle: null,
      }, opts || {});

      this.vue = null;       //  {i0,i1} en indices de bougies FINES ; null = tout
      this.cycle = null;     //  le cycle étudié, ou null
      this.vueAvant = null;  //  la vue d'avant l'entrée en cycle, à restituer
      this.kSurvol = null;   //  chandelier sous le réticule
      this.epingle = false;
      this.anim = null;
      this.marques = [];
      this.parK = {};

      this.svg = el("svg", { role: "img",
        "aria-label": this.o.aria || "graphique de marché" });
      this.svg.style.display = "block";
      //  pan-y et non none : ce graphique se déplace au doigt, mais confisquer
      //  le geste vertical en permanence prendrait un demi-écran de défilement
      //  en échange de rien. touch-action passe à none le temps d'un geste,
      //  entre pointerdown et pointerup, et seulement là.
      this.svg.style.touchAction = "pan-y";
      conteneur.appendChild(this.svg);

      this.info = document.createElement("div");
      this.info.className = "marche-info";
      this.info.hidden = true;
      conteneur.appendChild(this.info);

      this.l = 0; this.h = 0;
      if (global.ResizeObserver) {
        this.ro = new ResizeObserver(() => this.rendre());
        this.ro.observe(conteneur);
      }
      this._surResize = () => this.rendre();
      global.addEventListener("resize", this._surResize);

      if (this.o.gestes) this.brancherGestes();
      else {
        this.svg.addEventListener("pointermove", e => this.croix(e));
        this.svg.addEventListener("pointerleave", () => this.cacher());
      }

      //  Un changement de préférence redessine : forme des marques, fuseau,
      //  hauteur, aimantation. Le désabonnement est gardé pour le jour où un
      //  graphique sera détruit.
      if (global.Prefs && global.Prefs.sur)
        this._desabonner = global.Prefs.sur(() => this.rendre(true));

      this.rendre(true);
      requestAnimationFrame(() => this.rendre(true));
    }

    detruire() {
      if (this.ro) this.ro.disconnect();
      global.removeEventListener("resize", this._surResize);
      if (this._desabonner) this._desabonner();
    }

    majDonnees(d) {
      const autreSerie = d.bougies && d.bougies !== this.o.bougies;
      Object.assign(this.o, d);
      //  Changer de paire ou de robot sort du mode cycle et rend la vue entière :
      //  une fenêtre gardée d'un autre marché n'a aucun sens.
      if (autreSerie) { this.vue = null; this.cycle = null; this.vueAvant = null; }
      this.rendre(true);
    }

    /* ——— la fenêtre visible, en indices de bougies fines ——— */
    bornes() {
      const n = this.o.bougies.length;
      if (!n) return [0, -1];
      if (!this.vue) return [0, n - 1];
      return [borne(this.vue.i0, 0, n - 1), borne(this.vue.i1, 0, n - 1)];
    }

    /*  Agrège les bougies fines en chandeliers de `unite` bougies. Une bougie
     *  agrégée ouvre à la première ouverture et ferme à la dernière clôture :
     *  tout autre raccourci inventerait des mèches.
     *
     *  Les paquets sont ancrés sur une grille GLOBALE (i − i % u) et non sur le
     *  bord de la fenêtre : sans cela, chaque pixel de déplacement redécoupe les
     *  paquets et les chandeliers scintillent sous le doigt.  */
    agreger() {
      const b = this.o.bougies, u = Math.max(1, Math.round(this.o.unite));
      const bo = this.bornes(), i0 = bo[0], i1 = bo[1];
      if (i1 < i0) return [];
      const out = [];
      for (let i = i0 - (i0 % u); i <= i1; i += u) {
        const lot = b.slice(Math.max(0, i), Math.min(b.length, i + u));
        if (!lot.length) continue;
        if (u === 1) {
          out.push({ ts: lot[0][0], o: lot[0][1], h: lot[0][2], l: lot[0][3],
                     c: lot[0][4], v: lot[0][5] || 0 });
        } else {
          let hh = -Infinity, ll = Infinity, vv = 0;
          for (const x of lot) {
            if (x[2] > hh) hh = x[2];
            if (x[3] < ll) ll = x[3];
            vv += x[5] || 0;
          }
          out.push({ ts: lot[0][0], o: lot[0][1], h: hh, l: ll,
                     c: lot[lot.length - 1][4], v: vv });
        }
      }
      return out;
    }

    rendre(force) {
      const l = Math.max(240, Math.round(this.el.clientWidth || 0));
      if (!l) return;
      const base = typeof this.o.hauteur === "function" ? this.o.hauteur(l) : this.o.hauteur;
      const h = Math.round(base * prefs().echelleHauteur);
      if (!force && l === this.l && h === this.h) return;
      this.l = l; this.h = h;
      this.svg.setAttribute("width", l);
      this.svg.setAttribute("height", h);
      this.svg.setAttribute("viewBox", "0 0 " + l + " " + h);
      this.svg.replaceChildren();
      this.dessiner(l, h);
      this.legende();     //  au repos : la dernière bougie
    }

    dessiner(W, H) {
      const P = prefs();
      const MB = 22, MT = 26;     //  MT monte de 10 à 26 : la légende fixe y loge
      const vis = this.agreger();
      if (!vis.length) {
        el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 12,
          fill: "var(--ink-3)" }, this.svg).textContent = "pas encore de prix";
        this.geo = null;
        return;
      }

      let lo = Infinity, hi = -Infinity, vmax = 0;
      for (const b of vis) {
        if (b.l < lo) lo = b.l;
        if (b.h > hi) hi = b.h;
        if (b.v > vmax) vmax = b.v;
      }
      //  Les niveaux marqués « cadrer » tirent l'échelle pour rester visibles :
      //  un prix de revient hors du cadre ne sert à rien. Les barreaux du bas,
      //  eux, sont souvent à −50 % et écraseraient tout le graphique.
      for (const n of this.o.niveaux) {
        if (!n.cadrer || !isFinite(n.prix)) continue;
        lo = Math.min(lo, n.prix); hi = Math.max(hi, n.prix);
      }
      //  Rembourrage ASYMÉTRIQUE : l'air se met là où se posent les étiquettes,
      //  c'est-à-dire en haut. Six pour cent des deux côtés en gaspillaient en bas.
      const etendue = (hi - lo) || Math.abs(hi) * 0.02 || 1;
      hi += etendue * 0.10; lo -= etendue * 0.05;

      //  L'axe se dimensionne sur son plus long libellé.
      const gradus = ticks(lo, hi, Math.max(3, Math.round((H - MT - MB) / 52)));
      let largeMax = 0;
      for (const v of gradus)
        largeMax = Math.max(largeMax, mesurer(prixCourt(v), POLICE_AXE));
      const MR = Math.round(borne(largeMax + 16, 52, 110));

      const hVol = this.o.volume ? Math.round((H - MT - MB) * 0.16) : 0;
      const hPrix = H - MT - MB - hVol;
      const iw = W - MR;
      const pas = iw / vis.length;
      const X = k => (k + 0.5) * pas;
      const Y = v => MT + (1 - (v - lo) / (hi - lo)) * hPrix;
      const YV = v => MT + hPrix + hVol - (vmax ? v / vmax : 0) * hVol;
      const tsMin = vis[0].ts, span = this.o.pas * this.o.unite;
      const kDe = ts => Math.round((ts - tsMin) / span);
      this.geo = { W, H, MR, MB, MT, hPrix, hVol, iw, pas, vis, X, Y, YV,
                   lo, hi, kDe, span };

      /*  Le cycle étudié, en indices de chandeliers : tout ce qui est dehors
       *  s'atténue. On met en évidence, on n'isole pas — le reste de la série
       *  reste visible, sinon on ne voit plus où le cycle se situe. L'atténuation
       *  porte sur des GROUPES DE DESSIN et jamais sur du texte : un chiffre à
       *  moitié transparent est un chiffre moins lisible.  */
      const cy = this.cycle;
      const ka = cy ? kDe(cy.t0) : 0, kb = cy ? kDe(cy.t1) : 0;
      const dedans = k => !cy || (k >= ka && k <= kb);

      // --- grille et axe des prix, à DROITE : convention des plateformes ---
      for (const v of gradus) {
        const y = Math.round(Y(v)) + 0.5;
        el("line", { x1: 0, x2: iw, y1: y, y2: y, stroke: "var(--grille)",
          "stroke-width": 1 }, this.svg);
        el("line", { x1: iw, x2: iw + 4, y1: y, y2: y, stroke: "var(--rule-fort)",
          "stroke-width": 1 }, this.svg);
        el("text", { x: iw + 8, y: y + 3.5, "font-size": 11, fill: "var(--ink-3)" },
          this.svg).textContent = prixCourt(v);
      }
      const xAxe = Math.round(iw) + 0.5;
      el("line", { x1: xAxe, x2: xAxe, y1: MT, y2: MT + hPrix + hVol,
        stroke: "var(--rule-fort)", "stroke-width": 1 }, this.svg);

      //  Cent dix pixels et non quatre-vingt-huit : a quatre-vingt-huit, une
      //  etiquette « 08/09 19h » en fait cinquante-neuf et la premiere, calee a
      //  gauche, touchait la deuxieme a un demi-pixel pres.
      const nT = Math.max(2, Math.floor(iw / 110));
      const saut = Math.max(1, Math.ceil(vis.length / nT));
      for (let k = 0; k < vis.length; k += saut) {
        const x = Math.round(X(k)) + 0.5;
        el("line", { x1: x, x2: x, y1: MT, y2: MT + hPrix + hVol,
          stroke: "var(--grille)", "stroke-width": 1 }, this.svg);
        el("text", { x: x, y: H - 7, "font-size": 11, fill: "var(--ink-3)",
          "text-anchor": k === 0 ? "start" : (k + saut >= vis.length ? "end" : "middle") },
          this.svg).textContent = span >= 86400000 ? jj(vis[k].ts) : jjh(vis[k].ts);
      }

      // --- périodes où du capital dort, DERRIÈRE les chandeliers ---
      for (const bande of this.o.bandes) {
        const k0 = Math.max(0, kDe(bande[0])), k1 = Math.min(vis.length - 1, kDe(bande[1]));
        if (k1 < 0 || k0 > vis.length - 1) continue;
        const surLeCycle = cy && k0 >= ka - 1 && k1 <= kb + 1;
        el("rect", { x: X(k0) - pas / 2, y: MT,
          width: Math.max(1.5, X(k1) - X(k0) + pas), height: hPrix,
          fill: "var(--bande)", opacity: cy ? (surLeCycle ? 1 : 0.4) : 1 }, this.svg);
      }

      // --- volume ---
      if (this.o.volume && vmax > 0) {
        const gv = el("g", {}, this.svg);
        for (let k = 0; k < vis.length; k++) {
          const b = vis[k];
          const L = Math.max(0.6, Math.floor(pas * 0.7));
          el("rect", { x: Math.round(X(k) - L / 2), y: Math.round(YV(b.v)),
            width: L, height: Math.max(0.5, Math.round(MT + hPrix + hVol - YV(b.v))),
            fill: b.c >= b.o ? "var(--chandelier-h)" : "var(--chandelier-b)",
            opacity: dedans(k) ? 0.32 : 0.16 }, gv);
        }
        const yv = Math.round(MT + hPrix) + 0.5;
        el("line", { x1: 0, x2: iw, y1: yv, y2: yv, stroke: "var(--rule-fort)",
          "stroke-width": 1 }, this.svg);
      }

      /*  --- niveaux : l'échelle d'achat, le prix de revient, la cible ---
       *  L'étiquette se pose À DROITE, contre l'axe. À gauche, elle tombait sur
       *  les chandeliers des premières heures et devenait illisible dès qu'un
       *  achat avait lieu tôt.
       *
       *  DEUX ÉTIQUETTES NE SE RECOUVRENT JAMAIS. Le prix de revient et le
       *  barreau du haut tombent souvent à quatre pixels l'un de l'autre — on
       *  lisait « -2 %evi€t ». Celle qui arrive ensuite recule vers la gauche
       *  de la largeur de celle qui est déjà là.
       *
       *  Un barreau hors cadre devient un chevron sur le bord : il existe, on
       *  sait de quel côté, et il ne déforme pas l'échelle. Ils s'empilent —
       *  posés tous au même endroit, les deux barreaux profonds s'écrivaient
       *  l'un sur l'autre.  */
      const POLICE_NIV = '400 9.5px "IBM Plex Mono", ui-monospace, monospace';
      const posees = [];
      const etiquette = (texte, y, col, opac) => {
        const w = mesurer(texte, POLICE_NIV);
        let xf = iw - 5;
        for (const q of posees) if (Math.abs(q.y - y) < 11) xf = Math.min(xf, q.x0 - 8);
        el("text", { x: xf, y: y - 3.5, "text-anchor": "end", "font-size": 9.5,
          fill: col, opacity: opac == null ? 1 : opac }, this.svg).textContent = texte;
        posees.push({ y: y, x0: xf - w });
      };

      const horsBas = [], horsHaut = [];
      for (const n of this.o.niveaux) {
        if (!isFinite(n.prix)) continue;
        if (n.prix < lo) { horsBas.push(n); continue; }
        if (n.prix > hi) { horsHaut.push(n); continue; }
        const col = n.couleur || "var(--rule-fort)";
        const y = Math.round(Y(n.prix)) + 0.5;
        el("line", { x1: 0, x2: iw, y1: y, y2: y, stroke: col,
          "stroke-width": n.epais || 1, "stroke-dasharray": n.pointille || "4 4",
          opacity: n.opacite || 0.9 }, this.svg);
        if (n.texte) etiquette(n.texte, y, col);
      }
      /*  DEUX CHEVRONS AU PLUS PAR CÔTÉ, puis un décompte. Sur l'échelle à huit
       *  barreaux vue de près, six barreaux sortent du cadre : six chevrons
       *  empilés faisaient une colonne de texte minuscule posée en travers des
       *  chandeliers, qui ne disait rien de plus que « il y en a encore ». Le
       *  rail, sous le graphique, porte de toute façon la géométrie complète.  */
      const chevron = (liste, bas) => {
        const s = bas ? 1 : -1;
        const trait = (y, col, texte, opac) => {
          el("path", { d: "M" + (iw - 20) + " " + y + "l4 " + (4 * s) + "l4 " + (-4 * s),
            fill: "none", stroke: col, "stroke-width": 1.4, "stroke-linecap": "round",
            "stroke-linejoin": "round", opacity: opac }, this.svg);
          el("text", { x: iw - 26, y: y + 3.5, "text-anchor": "end", "font-size": 9.5,
            fill: col, opacity: opac }, this.svg).textContent = texte;
        };
        const nommes = liste.filter(n => n.texte);
        const montres = nommes.slice(0, 2);
        let i = 0;
        for (const n of montres) {
          const y = bas ? MT + hPrix - 7 - i * 13 : MT + 7 + i * 13;
          if (y < MT + 4 || y > MT + hPrix - 4) return;
          trait(y, n.couleur || "var(--rule-fort)", n.texte, 0.85);
          i++;
        }
        const reste = nommes.length - montres.length;
        if (reste > 0) {
          const y = bas ? MT + hPrix - 7 - i * 13 : MT + 7 + i * 13;
          if (y >= MT + 4 && y <= MT + hPrix - 4)
            trait(y, "var(--ink-3)",
              "+" + reste + (bas ? " plus bas" : " plus haut"), 0.7);
        }
      };
      chevron(horsBas, true);
      chevron(horsHaut, false);

      /*  --- chandeliers ---
       *  Sous un pixel de large, le corps devient un trait : on ne masque pas la
       *  densité, on la laisse se voir. Au-dessus, tout s'aligne sur des entiers
       *  et le corps garde la PARITÉ de la mèche, sinon la mèche d'un pixel n'est
       *  pas centrée sur son corps et le graphique paraît flou sans qu'on sache
       *  dire pourquoi.  */
      const gcOut = el("g", cy ? { opacity: "var(--veille-bougies)" } : {}, this.svg);
      const gcIn = el("g", {}, this.svg);
      const mince = pas < 2.5;
      let L = 1;
      if (!mince) {
        const coeff = 1 - 0.2 * Math.atan(Math.max(4, pas) - 4) / (Math.PI / 2);
        L = Math.max(1, Math.floor(Math.min(pas * coeff, pas - 1)));
        if (L >= 2 && (L % 2) !== 1) L -= 1;     //  même parité que la mèche
      }
      for (let k = 0; k < vis.length; k++) {
        const b = vis[k];
        const g = dedans(k) ? gcIn : gcOut;
        const col = b.c >= b.o ? "var(--chandelier-h)" : "var(--chandelier-b)";
        if (mince) {
          el("line", { x1: X(k), x2: X(k), y1: Y(b.h), y2: Y(b.l), stroke: col,
            "stroke-width": Math.max(0.7, pas * 0.8) }, g);
          continue;
        }
        const xc = Math.round(X(k)) + 0.5;
        el("line", { x1: xc, x2: xc, y1: Math.round(Y(b.h)), y2: Math.round(Y(b.l)),
          stroke: col, "stroke-width": 1 }, g);
        el("rect", { x: xc - (L - 1) / 2 - 0.5,
          y: Math.round(Y(Math.max(b.o, b.c))), width: L,
          height: Math.max(1, Math.round(Math.abs(Y(b.o) - Y(b.c)))), fill: col }, g);
      }

      /*  --- les courbes posées sur les prix : prix de revient, cible de vente ---
       *  Elles bougent dans le temps, contrairement aux niveaux qui sont des
       *  horizontales. C'est par elles que se voit le mécanisme central de la
       *  stratégie : le prix de revient qui descend plus vite que le marché.  */
      for (const c of this.o.courbes) {
        if (!c.points || c.points.length < 2) continue;
        let d = "", ouvert = false;
        for (const pt of c.points) {
          const k = kDe(pt[0]), v = pt[1];
          if (k < 0 || k >= vis.length || !isFinite(v)) { ouvert = false; continue; }
          d += (ouvert ? "L" : "M") + X(k).toFixed(1) + " " + Y(v).toFixed(1) + " ";
          ouvert = true;
        }
        if (!d) continue;
        el("path", { d: d, fill: "none", stroke: c.couleur || "var(--ink-3)",
          "stroke-width": (c.epais || 1.6) * (cy ? 1.25 : 1),
          "stroke-dasharray": c.pointille || "none",
          "stroke-linejoin": "round", opacity: c.opacite || 0.95 }, this.svg);
        //  l'étiquette se pose au bout de la courbe, là où l'œil la quitte
        const dernier = c.points[c.points.length - 1];
        const kd = kDe(dernier[0]);
        if (c.texte && kd >= 0 && kd < vis.length)
          el("text", { x: Math.min(iw - 2, X(kd) + 5), y: Y(dernier[1]) - 5,
            "text-anchor": X(kd) + 60 > iw ? "end" : "start", "font-size": 10,
            fill: c.couleur || "var(--ink-3)", "font-weight": 600 },
            this.svg).textContent = c.texte;
      }

      // --- les ordres, posés sur le chandelier où ils ont eu lieu ---
      this.parK = {}; this.marques = [];
      const goOut = el("g", cy ? { opacity: "var(--veille)" } : {}, this.svg);
      const goIn = el("g", {}, this.svg);
      let eMax = 1;
      for (const o of this.o.ordres) if ((o.euros || 0) > eMax) eMax = o.euros;
      for (const o of this.o.ordres) {
        const k = kDe(o.ts);
        if (k < 0 || k >= vis.length) continue;
        const x = X(k), y = Y(o.prix);
        if (y < MT - 4 || y > MT + hPrix + 4) continue;
        const brut = P.proportionnel ? 4 + 3.5 * Math.sqrt((o.euros || 0) / eMax) : 6;
        this.marque(dedans(k) ? goIn : goOut, x, y, brut * P.tailleMarque,
                    o.genre === "achat");
        (this.parK[k] || (this.parK[k] = [])).push(o);
        this.marques.push({ x: x, y: y, o: o, k: k });
      }

      // --- dernier prix, sur l'axe : le repérage le plus utilisé ---
      const der = vis[vis.length - 1];
      const yd = Math.round(Y(der.c)) + 0.5;
      el("line", { x1: 0, x2: iw, y1: yd, y2: yd, stroke: "var(--ink-3)",
        "stroke-width": 1, "stroke-dasharray": "4 3", opacity: 0.8 }, this.svg);
      el("rect", { x: iw + 1, y: yd - 9, width: MR - 2, height: 18, rx: 3,
        fill: der.c >= der.o ? "var(--hausse-plein)" : "var(--baisse-plein)" }, this.svg);
      el("text", { x: iw + 6, y: yd + 4, fill: "var(--sur-vif)", "font-size": 11,
        "font-weight": 600 }, this.svg).textContent = prixCourt(der.c);

      this.gCroix = el("g", { "pointer-events": "none" }, this.svg);
      this.gLegende = el("g", { "pointer-events": "none" }, this.svg);
    }

    marque(g, x, y, t, achat) { dessinerMarque(g, x, y, t, achat); }

    /* ————————————————————————————————————————————————————————————
     *  La légende fixe. Elle ne disparaît jamais : au repos elle décrit la
     *  dernière bougie, au survol celle sous le réticule. C'est ce qui permet à
     *  l'infobulle de ne plus porter l'OHLC — donc de ne plus recouvrir les
     *  chandeliers qu'elle décrivait.
     * ———————————————————————————————————————————————————————————— */
    legende(k) {
      const g = this.geo;
      if (!g || !this.gLegende) return;
      this.gLegende.replaceChildren();
      const vis = g.vis;
      const b = vis[k == null ? vis.length - 1 : borne(k, 0, vis.length - 1)];
      if (!b) return;
      const pc = b.o ? (b.c / b.o - 1) : 0;
      const P = prefs();

      const tete = (this.o.nom ? this.o.nom + " · " : "") + libellePas(g.span)
        + (P.utc ? "" : " · heure locale");
      const cloture = prixCourt(b.c) + " (" + (pc >= 0 ? "+" : "")
        + (pc * 100).toFixed(2).replace(".", ",") + " %)";
      const colC = pc >= 0 ? "var(--hausse)" : "var(--baisse)";

      const complet = [
        { t: tete, c: "var(--ink)", w: 600 },
        { t: "O", c: "var(--ink-3)" }, { t: prixCourt(b.o), c: "var(--ink)" },
        { t: "H", c: "var(--ink-3)" }, { t: prixCourt(b.h), c: "var(--ink)" },
        { t: "B", c: "var(--ink-3)" }, { t: prixCourt(b.l), c: "var(--ink)" },
        { t: "C", c: "var(--ink-3)" }, { t: cloture, c: colC, w: 500 },
      ];
      //  Trop étroit pour la ligne complète : on garde la tête et la clôture,
      //  qui sont ce qu'on lit en premier. Rien n'est tronqué en plein mot.
      const court = [complet[0], { t: cloture, c: colC, w: 500 }];

      const poser = (morceaux) => {
        let x = 10;
        const out = [];
        for (const m of morceaux) {
          const w = mesurer(m.t, police(m.w === 600 ? "600" : "400"));
          out.push({ m: m, x: x, w: w });
          x += w + 7;
        }
        return out;
      };
      let pose = poser(complet);
      let fin = pose[pose.length - 1];
      if (fin.x + fin.w + 12 > g.iw) { pose = poser(court); fin = pose[pose.length - 1]; }

      el("rect", { x: 6, y: 3, width: Math.min(g.iw - 8, fin.x + fin.w),
        height: 18, rx: 4, fill: "var(--surface-haut)", opacity: 0.88 }, this.gLegende);
      for (const p of pose)
        el("text", { x: p.x, y: 16, "font-size": 11.5, fill: p.m.c,
          "font-weight": p.m.w || 400 }, this.gLegende).textContent = p.m.t;
    }

    /* ———————————————————— le réticule ———————————————————— */
    croix(evt) {
      const g = this.geo;
      if (!g || !this.gCroix || this.epingle) return;
      const r = this.svg.getBoundingClientRect();
      this.lire(evt.clientX - r.left, evt.clientY - r.top, evt.altKey);
    }

    lire(x, y, libre) {
      const g = this.geo;
      if (!g || !this.gCroix) return;
      this.gCroix.replaceChildren();
      if (x < 0 || x > g.iw || y < 0 || y > g.H) { this.cacher(); return; }
      const k = borne(Math.floor(x / g.pas), 0, g.vis.length - 1);
      const b = g.vis[k];
      this.kSurvol = k;
      const xk = Math.round(g.X(k)) + 0.5;

      /*  Aimanté sur la clôture par défaut : un réticule libre affiche un prix
       *  INTERPOLÉ, qui n'existe dans aucune donnée. Alt le libère le temps d'un
       *  geste, et le réglage le libère pour de bon.  */
      const aimante = prefs().aimante && !libre;
      const yl = aimante ? Math.round(g.Y(b.c)) + 0.5 : Math.round(y) + 0.5;
      const prix = aimante ? b.c : g.lo + (1 - (y - g.MT) / g.hPrix) * (g.hi - g.lo);

      el("line", { x1: xk, x2: xk, y1: g.MT, y2: g.H - g.MB, stroke: "var(--reticule)",
        "stroke-width": 1, "stroke-dasharray": "3 3" }, this.gCroix);
      if (aimante || (y >= g.MT && y <= g.MT + g.hPrix))
        el("line", { x1: 0, x2: g.iw, y1: yl, y2: yl, stroke: "var(--reticule)",
          "stroke-width": 1, "stroke-dasharray": "3 3" }, this.gCroix);

      if (yl >= g.MT - 1 && yl <= g.MT + g.hPrix + 1) {
        el("rect", { x: g.iw + 1, y: yl - 9, width: g.MR - 2, height: 18, rx: 3,
          fill: "var(--ink)" }, this.gCroix);
        el("text", { x: g.iw + 6, y: yl + 4, fill: "var(--ground)", "font-size": 11 },
          this.gCroix).textContent = prixCourt(prix);
      }
      //  L'étiquette de DATE sous la verticale, en miroir de la pastille de prix :
      //  le réticule disait où l'on était en prix, jamais en temps.
      const lib = g.span >= 86400000 ? jj(b.ts) : jjhm(b.ts);
      const w = mesurer(lib, POLICE_AXE) + 12;
      const xr = borne(xk - w / 2, 0, Math.max(0, g.iw - w));
      el("rect", { x: xr, y: g.H - g.MB + 2, width: w, height: 16, rx: 3,
        fill: "var(--ink)" }, this.gCroix);
      el("text", { x: xr + w / 2, y: g.H - g.MB + 13.5, "text-anchor": "middle",
        fill: "var(--ground)", "font-size": 11 }, this.gCroix).textContent = lib;

      this.legende(k);
      this.bulle(k, x, y);
    }

    /*  L'infobulle ne porte plus QUE les ordres : l'OHLC est monté dans la
     *  légende fixe. Quand il n'y a pas d'ordre sur la bougie, elle ne s'affiche
     *  pas du tout — c'est-à-dire la plupart du temps.  */
    bulle(k, x, y) {
      const ordres = this.parK[k] || [];
      const g = this.geo;
      if (!ordres.length || !g) { this.info.hidden = true; return; }
      let html = "<b>" + (g.span >= 86400000 ? jj(g.vis[k].ts) : jjhm(g.vis[k].ts)) + "</b>";
      for (const o of ordres)
        html += '<span class="ordre ' + o.genre + '">' + o.genre + " "
          + prixCourt(o.prix) + " · " + (o.euros || 0).toFixed(2) + " €"
          + (o.gain != null ? " · gain " + (o.gain >= 0 ? "+" : "") + o.gain.toFixed(2) + " €" : "")
          + "</span>";
      if (this.o.cycles && this.o.cycles.length && !this.cycle)
        html += '<span class="astuce">cliquer pour étudier ce cycle</span>';
      this.info.innerHTML = html;
      this.info.hidden = false;
      const large = this.info.offsetWidth || 150;
      this.info.style.left = (x + 14 + large > g.W ? x - large - 14 : x + 14) + "px";
      this.info.style.top = borne(y - 10, 4, Math.max(4, g.H - 90)) + "px";
    }

    cacher() {
      if (this.epingle) return;
      if (this.gCroix) this.gCroix.replaceChildren();
      this.info.hidden = true;
      this.kSurvol = null;
      this.legende();
    }

    /* ————————————————————————————————————————————————————————————
     *  Les gestes : molette, glissement, pincement, double-clic, clavier.
     *  Le seuil de sept pixels sépare la lecture du déplacement — sans lui, un
     *  clic un peu tremblé passe pour un glissement et la vue saute.
     * ———————————————————————————————————————————————————————————— */
    brancherGestes() {
      const s = this.svg;
      const pts = new Map();
      let depart = null, bouge = false, ecart0 = 0, span0 = 0, i00 = 0;

      s.addEventListener("pointermove", e => {
        if (pts.has(e.pointerId)) pts.set(e.pointerId, e);
        if (pts.size === 2 && span0) {
          this.pincer(Array.from(pts.values()), ecart0, span0, i00);
          return;
        }
        if (depart) {
          const dx = e.clientX - depart.x, dy = e.clientY - depart.y;
          if (!bouge && Math.hypot(dx, dy) > 7) { bouge = true; s.style.touchAction = "none"; }
          if (bouge) { this.glisser(dx, depart.i0, depart.i1); return; }
        }
        this.croix(e);
      });
      s.addEventListener("pointerleave", () => { if (!depart) this.cacher(); });

      s.addEventListener("pointerdown", e => {
        pts.set(e.pointerId, e);
        if (pts.size === 2) {
          const deux = Array.from(pts.values());
          ecart0 = Math.abs(deux[0].clientX - deux[1].clientX) || 1;
          const bo = this.bornes();
          span0 = bo[1] - bo[0] + 1; i00 = bo[0];
          depart = null;
          s.style.touchAction = "none";
          return;
        }
        const bo = this.bornes();
        depart = { x: e.clientX, y: e.clientY, i0: bo[0], i1: bo[1] };
        bouge = false;
        try { s.setPointerCapture(e.pointerId); } catch (err) { /* pas de capture */ }
      });

      const relacher = e => {
        pts.delete(e.pointerId);
        if (pts.size < 2) { ecart0 = 0; span0 = 0; }
        if (depart && !bouge) this.clic(e);
        depart = null; bouge = false;
        if (!pts.size) s.style.touchAction = "pan-y";
      };
      s.addEventListener("pointerup", relacher);
      s.addEventListener("pointercancel", relacher);

      s.addEventListener("wheel", e => {
        if (!this.o.bougies.length || !this.geo) return;
        e.preventDefault();
        const r = s.getBoundingClientRect();
        this.zoomer(e.deltaY > 0 ? 1.25 : 0.8, (e.clientX - r.left) / this.geo.iw);
      }, { passive: false });

      s.addEventListener("dblclick", () => this.toutRevoir());

      //  Au clavier, le graphique devient une application : sans tabindex il est
      //  simplement absent pour qui n'a pas de souris.
      s.setAttribute("tabindex", "0");
      s.setAttribute("role", "application");
      s.addEventListener("keydown", e => this.touche(e));
    }

    clic(e) {
      const r = this.svg.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      const m = this.marqueSous(x, y);
      if (m && this.o.cycles && this.o.cycles.length) {
        const c = this.cycleDe(m.o.ts);
        if (c) {
          if (this.cycle && this.cycle.t0 === c.t0) this.quitterCycle();
          else this.etudierCycle(c);
          return;
        }
      }
      //  Pas de marque sous le doigt : on épingle la lecture, ce qui rend le
      //  graphique utilisable au doigt — un survol n'existe pas sur un écran
      //  tactile, et sans épinglage la lecture y était inaccessible.
      if (this.epingle) { this.epingle = false; this.cacher(); }
      else { this.lire(x, y, e.altKey); this.epingle = true; }
    }

    marqueSous(x, y) {
      let best = null, d0 = 14;
      for (const m of this.marques) {
        const d = Math.hypot(m.x - x, m.y - y);
        if (d < d0) { d0 = d; best = m; }
      }
      return best;
    }

    cycleDe(ts) {
      for (const c of this.o.cycles) if (ts >= c.t0 && ts <= c.t1) return c;
      return null;
    }

    /* ———————————————— le mode cycle ———————————————— */
    etudierCycle(c) {
      if (!c) return;
      if (!this.cycle) this.vueAvant = this.vue ? { i0: this.vue.i0, i1: this.vue.i1 } : null;
      this.cycle = c;
      const n = this.o.bougies.length;
      if (n) {
        const t0 = this.o.bougies[0][0];
        const a = borne(Math.round((c.t0 - t0) / this.o.pas), 0, n - 1);
        const b = borne(Math.round((c.t1 - t0) / this.o.pas), 0, n - 1);
        const marge = Math.max(4 * this.o.unite, Math.round((b - a + 1) * 0.12));
        this.aller(borne(a - marge, 0, n - 1), borne(b + marge, 0, n - 1));
      } else {
        this.rendre(true);
      }
      this.epingle = false;
      this.info.hidden = true;
      if (this.o.surCycle) this.o.surCycle(c, this);
    }

    quitterCycle() {
      if (!this.cycle) return;
      this.cycle = null;
      const v = this.vueAvant; this.vueAvant = null;
      //  On rend la place où l'on était AVANT d'entrer : sortir d'un cycle ne
      //  doit pas coûter la fenêtre qu'on avait mis du temps à cadrer.
      if (v) this.aller(v.i0, v.i1);
      else { this.vue = null; this.rendre(true); }
      if (this.o.surCycle) this.o.surCycle(null, this);
    }

    /*  Déplacement animé de la fenêtre. La VUE bouge, jamais les chiffres.  */
    aller(i0, i1) {
      const n = this.o.bougies.length;
      if (!n) return;
      const bo = this.bornes(), a0 = bo[0], a1 = bo[1];
      //  document.hidden : dans un onglet en arriere-plan, requestAnimationFrame
      //  ne se declenche pas du tout. Sans ce garde-fou, la vue resterait figee
      //  a mi-chemin — ou, ici, ne bougerait jamais — et le graphique mentirait
      //  sur ce qu'il montre au retour.
      const reduit = document.hidden
        || matchMedia("(prefers-reduced-motion: reduce)").matches
        || document.documentElement.dataset.mouvement === "reduit";
      if (this.anim) { cancelAnimationFrame(this.anim); this.anim = null; }
      if (reduit || (a0 === i0 && a1 === i1)) {
        this.vue = { i0: i0, i1: i1 }; this.rendre(true); return;
      }
      const t0 = performance.now(), ms = 240;
      const pas = t => {
        const u = Math.min(1, (t - t0) / ms);
        const e = 1 - Math.pow(1 - u, 3);
        this.vue = { i0: Math.round(a0 + (i0 - a0) * e), i1: Math.round(a1 + (i1 - a1) * e) };
        this.rendre(true);
        this.anim = u < 1 ? requestAnimationFrame(pas) : null;
      };
      this.anim = requestAnimationFrame(pas);
    }

    /* ———————————————— zoom et déplacement ———————————————— */
    zoomer(facteur, fraction) {
      const n = this.o.bougies.length;
      if (!n) return;
      const u = Math.max(1, Math.round(this.o.unite));
      const bo = this.bornes(), i0 = bo[0], i1 = bo[1];
      const span = i1 - i0 + 1;
      const f = borne(fraction, 0, 1);
      const ancre = i0 + f * span;
      const neuf = borne(Math.round(span * facteur), Math.min(n, MINI * u), n);
      const deb = borne(Math.round(ancre - f * neuf), 0, n - neuf);
      this.vue = { i0: deb, i1: deb + neuf - 1 };
      this.rendre(true);
    }

    glisser(dx, i0, i1) {
      const n = this.o.bougies.length;
      if (!n || !this.geo) return;
      const span = i1 - i0 + 1;
      const parBougie = this.geo.iw / span;
      const deb = borne(i0 + Math.round(-dx / parBougie), 0, n - span);
      this.vue = { i0: deb, i1: deb + span - 1 };
      this.rendre(true);
    }

    pincer(deux, ecart0, span0, i00) {
      const n = this.o.bougies.length;
      if (!n) return;
      const u = Math.max(1, Math.round(this.o.unite));
      const ecart = Math.abs(deux[0].clientX - deux[1].clientX) || 1;
      const span = borne(Math.round(span0 * ecart0 / ecart), Math.min(n, MINI * u), n);
      const centre = i00 + span0 / 2;
      const deb = borne(Math.round(centre - span / 2), 0, n - span);
      this.vue = { i0: deb, i1: deb + span - 1 };
      this.rendre(true);
    }

    toutRevoir() {
      this.vue = null;
      if (this.cycle) {
        this.cycle = null; this.vueAvant = null;
        if (this.o.surCycle) this.o.surCycle(null, this);
      }
      this.rendre(true);
    }

    touche(e) {
      const n = this.o.bougies.length;
      if (!n || !this.geo) return;
      const bo = this.bornes(), i0 = bo[0], i1 = bo[1];
      const span = i1 - i0 + 1;
      const k = this.kSurvol == null ? this.geo.vis.length - 1 : this.kSurvol;
      const pasEcran = this.geo.iw * 0.1;
      let fait = true;
      switch (e.key) {
        case "ArrowRight": e.shiftKey ? this.glisser(-pasEcran, i0, i1) : this.viser(k + 1); break;
        case "ArrowLeft": e.shiftKey ? this.glisser(pasEcran, i0, i1) : this.viser(k - 1); break;
        case "+": case "=": this.zoomer(0.8, 0.5); break;
        case "-": this.zoomer(1.25, 0.5); break;
        case "Home": this.viser(0); break;
        case "End": this.viser(this.geo.vis.length - 1); break;
        case "Escape":
          if (this.cycle) this.quitterCycle();
          else { this.epingle = false; this.cacher(); }
          break;
        case "Enter": case " ": this.ouvrirSousReticule(); break;
        default: fait = false;
      }
      if (fait) e.preventDefault();
    }

    viser(k) {
      const g = this.geo;
      if (!g) return;
      const kk = borne(k, 0, g.vis.length - 1);
      this.epingle = false;
      this.lire(g.X(kk), g.Y(g.vis[kk].c), false);
      this.epingle = true;
    }

    ouvrirSousReticule() {
      if (this.cycle) { this.quitterCycle(); return; }
      const k = this.kSurvol;
      if (k == null || !this.o.cycles.length) return;
      const ordres = this.parK[k] || [];
      if (!ordres.length) return;
      const c = this.cycleDe(ordres[0].ts);
      if (c) this.etudierCycle(c);
    }
  }

  /*  La vignette de légende : le même dessin, à taille fixe, dans un carré de
   *  quatorze pixels. La page ne redécrit plus la forme dans son HTML.  */
  Marche.vignette = function (achat, cote) {
    const c = cote || 14;
    const s = el("svg", { width: c, height: c, viewBox: "0 0 " + c + " " + c,
      "aria-hidden": "true" });
    s.style.display = "inline-block";
    s.style.verticalAlign = "-2px";
    s.style.flex = "none";
    dessinerMarque(s, c / 2, c / 2, c * 0.34, achat);
    return s;
  };

  /*  Les vignettes de légende des pages, tenues à jour toutes seules. Une page
   *  écrit <span data-vignette="achat">achat</span> et n'a plus jamais à savoir
   *  quelle forme est choisie — sans quoi la légende annonce des triangles
   *  pendant que le graphique dessine des ronds.  */
  let _vignettesBranchees = false;
  Marche.poserVignettes = function (racine) {
    const r = racine || document;
    for (const n of r.querySelectorAll("[data-vignette]")) {
      const v = n.querySelector("svg");
      if (v) v.remove();
      n.insertBefore(Marche.vignette(n.dataset.vignette === "achat"), n.firstChild);
    }
    if (!_vignettesBranchees && global.Prefs && global.Prefs.sur) {
      _vignettesBranchees = true;
      global.Prefs.sur(() => Marche.poserVignettes());
    }
  };

  //  Exporte pour la page des simulations, qui dessine ses propres chandeliers
  //  mais doit poser exactement les memes marques.
  Marche.dessinerMarque = dessinerMarque;

  Marche.prixCourt = prixCourt;
  Marche.ticks = ticks;
  Marche.jjh = jjh;
  Marche.jjhm = jjhm;
  Marche.jj = jj;
  Marche.duree = duree;
  global.Marche = Marche;
})(window);
