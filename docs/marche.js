/*  Le graphique de marche, en un seul endroit.
 *
 *  La page des simulations et celle du paper trading montrent la meme chose :
 *  des chandeliers, une echelle d'achat posee dessus, des ordres, un reticule
 *  qui lit les prix. Deux implementations auraient derive l'une de l'autre —
 *  ce depot a deja paye cher d'avoir laisse deux verites coexister. Celle-ci
 *  ne depend d'aucune variable globale : tout entre par l'objet de creation.
 *
 *      const g = new Marche(conteneur, {
 *        bougies : [[ts,o,h,l,c,v], ...],   // au pas le plus fin disponible
 *        pas     : 300000,                   // millisecondes par bougie
 *        ordres  : [{ts, genre:"achat"|"vente", prix, euros, gain}],
 *        niveaux : [{prix, texte, classe, pointille}],   // echelle, revient, cible
 *        bandes  : [[tsDebut, tsFin]],       // periodes ou du capital dort
 *        unite   : 12,                       // bougies agregees par chandelier
 *      });
 *      g.majDonnees({bougies, ordres, niveaux});   // meme objet, autre contenu
 *
 *  Le dessin se fait en PIXELS REELS : un viewBox fige etire le texte a des
 *  tailles illisibles des que le conteneur change de largeur.
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
  function prixCourt(v) {
    if (!isFinite(v)) return "—";
    return nb(v, v >= 1000 ? 0 : v >= 10 ? 2 : v >= 0.1 ? 4 : 6);
  }
  const p2 = n => String(n).padStart(2, "0");
  function jjh(ts) {
    const d = new Date(ts);
    return p2(d.getUTCDate()) + "/" + p2(d.getUTCMonth() + 1) + " " +
           p2(d.getUTCHours()) + "h";
  }
  function jj(ts) {
    const d = new Date(ts);
    return p2(d.getUTCDate()) + "/" + p2(d.getUTCMonth() + 1);
  }

  /*  Des graduations que l'oeil lit : 1, 2, 2,5 ou 5 fois une puissance de dix.
   *  Un pas "joli" evite les axes gradues a 0,037 qui ne disent rien.  */
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

  class Marche {
    constructor(conteneur, opts) {
      this.el = conteneur;
      this.o = Object.assign({
        bougies: [], pas: 300000, ordres: [], niveaux: [], bandes: [],
        unite: 1, hauteur: 340, volume: true, titreAxe: "",
      }, opts || {});
      this.svg = el("svg", { role: "img", "aria-label": this.o.aria || "graphique de marche" });
      this.svg.style.display = "block";
      //  pan-y et non none : ce graphique n'a ni zoom ni deplacement au doigt, donc
      //  confisquer le geste vertical prenait un demi-ecran de defilement en echange
      //  de rien. Le reticule, lui, fonctionne toujours au survol et au glissement.
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
      global.addEventListener("resize", () => this.rendre());
      this.svg.addEventListener("pointermove", e => this.croix(e));
      this.svg.addEventListener("pointerleave", () => this.cacher());
      this.rendre(true);
      requestAnimationFrame(() => this.rendre(true));
    }

    majDonnees(d) {
      Object.assign(this.o, d);
      this.rendre(true);
    }

    /*  Agrege les bougies fines en chandeliers de `unite` bougies. Une bougie
     *  agregee ouvre a la premiere ouverture et ferme a la derniere cloture :
     *  tout autre raccourci inventerait des meches.  */
    agreger() {
      const b = this.o.bougies, u = Math.max(1, Math.round(this.o.unite));
      if (u === 1) return b.map(x => ({ ts: x[0], o: x[1], h: x[2], l: x[3], c: x[4], v: x[5] || 0 }));
      const out = [];
      for (let i = 0; i < b.length; i += u) {
        const lot = b.slice(i, i + u);
        out.push({
          ts: lot[0][0], o: lot[0][1],
          h: Math.max(...lot.map(x => x[2])), l: Math.min(...lot.map(x => x[3])),
          c: lot[lot.length - 1][4], v: lot.reduce((a, x) => a + (x[5] || 0), 0),
        });
      }
      return out;
    }

    rendre(force) {
      const l = Math.max(240, Math.round(this.el.clientWidth || 0));
      if (!l) return;
      const h = Math.round(typeof this.o.hauteur === "function"
        ? this.o.hauteur(l) : this.o.hauteur);
      if (!force && l === this.l && h === this.h) return;
      this.l = l; this.h = h;
      this.svg.setAttribute("width", l);
      this.svg.setAttribute("height", h);
      this.svg.setAttribute("viewBox", `0 0 ${l} ${h}`);
      this.svg.replaceChildren();
      this.dessiner(l, h);
    }

    dessiner(W, H) {
      const MR = 62, MB = 22, MT = 10;
      const vis = this.agreger();
      if (!vis.length) {
        el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 12,
          fill: "var(--ink-3)" }, this.svg).textContent = "pas encore de prix";
        return;
      }
      const hVol = this.o.volume ? Math.round((H - MT - MB) * 0.16) : 0;
      const hPrix = H - MT - MB - hVol;
      const iw = W - MR;
      let lo = Infinity, hi = -Infinity, vmax = 0;
      for (const b of vis) {
        if (b.l < lo) lo = b.l;
        if (b.h > hi) hi = b.h;
        if (b.v > vmax) vmax = b.v;
      }
      //  Les niveaux marques "cadrer" tirent l'echelle pour rester visibles :
      //  un prix de revient hors du cadre ne sert a rien. Les barreaux du bas,
      //  eux, sont souvent a -50 % et ecraseraient tout le graphique.
      for (const n of this.o.niveaux) {
        if (!n.cadrer || !isFinite(n.prix)) continue;
        lo = Math.min(lo, n.prix);
        hi = Math.max(hi, n.prix);
      }
      const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.01 || 1;
      lo -= pad; hi += pad;
      const pas = iw / vis.length;
      const X = k => (k + 0.5) * pas;
      const Y = v => MT + (1 - (v - lo) / (hi - lo)) * hPrix;
      const YV = v => MT + hPrix + hVol - (vmax ? v / vmax : 0) * hVol;
      this.geo = { W, H, MR, MB, MT, hPrix, hVol, iw, pas, vis, X, Y, lo, hi };

      //  --- grille et axe des prix, a DROITE : convention des plateformes ---
      for (const v of ticks(lo, hi, Math.max(3, Math.round(hPrix / 52)))) {
        el("line", { x1: 0, x2: iw, y1: Y(v), y2: Y(v), stroke: "var(--rule)",
          "stroke-width": 1 }, this.svg);
        el("text", { x: iw + 6, y: Y(v) + 4, "font-size": 10, fill: "var(--ink-3)",
          "font-family": '"IBM Plex Mono",monospace' }, this.svg)
          .textContent = prixCourt(v);
      }
      const nT = Math.max(2, Math.floor(iw / 88));
      const saut = Math.max(1, Math.ceil(vis.length / nT));
      for (let k = 0; k < vis.length; k += saut) {
        el("line", { x1: X(k), x2: X(k), y1: MT, y2: MT + hPrix + hVol,
          stroke: "var(--rule)", "stroke-width": 1 }, this.svg);
        el("text", { x: X(k), y: H - 7, "font-size": 10, fill: "var(--ink-3)",
          "font-family": '"IBM Plex Mono",monospace',
          "text-anchor": k === 0 ? "start" : (k + saut >= vis.length ? "end" : "middle") },
          this.svg).textContent =
          (this.o.pas * this.o.unite >= 86400000) ? jj(vis[k].ts) : jjh(vis[k].ts);
      }

      //  --- periodes ou du capital dort, DERRIERE les chandeliers ---
      const tsMin = vis[0].ts, span = this.o.pas * this.o.unite;
      const kDe = ts => Math.round((ts - tsMin) / span);
      for (const [a, b] of this.o.bandes) {
        const ka = Math.max(0, kDe(a)), kb = Math.min(vis.length - 1, kDe(b));
        if (kb < 0 || ka > vis.length - 1) continue;
        el("rect", { x: X(ka) - pas / 2, y: MT, width: Math.max(1.5, X(kb) - X(ka) + pas),
          height: hPrix, fill: "var(--achat)", opacity: 0.08 }, this.svg);
      }

      //  --- volume ---
      if (this.o.volume && vmax > 0) {
        const gv = el("g", {}, this.svg);
        for (let k = 0; k < vis.length; k++) {
          const b = vis[k];
          el("rect", { x: X(k) - pas * 0.35, y: YV(b.v), width: Math.max(0.6, pas * 0.7),
            height: Math.max(0.5, MT + hPrix + hVol - YV(b.v)),
            fill: b.c >= b.o ? "var(--hausse)" : "var(--baisse)", opacity: 0.3 }, gv);
        }
        el("line", { x1: 0, x2: iw, y1: MT + hPrix, y2: MT + hPrix,
          stroke: "var(--rule-fort)", "stroke-width": 1 }, this.svg);
      }

      //  --- niveaux : l'echelle d'achat, le prix de revient, la cible ---
      for (const n of this.o.niveaux) {
        if (!isFinite(n.prix) || n.prix < lo || n.prix > hi) continue;
        const y = Y(n.prix);
        el("line", { x1: 0, x2: iw, y1: y, y2: y, stroke: n.couleur || "var(--rule-fort)",
          "stroke-width": n.epais || 1,
          "stroke-dasharray": n.pointille || "4 4", opacity: n.opacite || 0.9 }, this.svg);
        if (n.texte)
          el("text", { x: 4, y: y - 3, "font-size": 9.5, fill: n.couleur || "var(--ink-3)",
            "font-family": '"IBM Plex Mono",monospace' }, this.svg).textContent = n.texte;
      }

      //  --- chandeliers : sous un pixel de large, le corps devient un trait.
      //      On ne masque pas la densite, on la laisse se voir. ---
      const gc = el("g", {}, this.svg);
      const mince = pas < 2.5;
      for (let k = 0; k < vis.length; k++) {
        const b = vis[k];
        const col = b.c >= b.o ? "var(--hausse)" : "var(--baisse)";
        el("line", { x1: X(k), x2: X(k), y1: Y(b.h), y2: Y(b.l), stroke: col,
          "stroke-width": mince ? Math.max(0.7, pas * 0.8) : 1 }, gc);
        if (!mince) {
          const y = Y(Math.max(b.o, b.c));
          el("rect", { x: X(k) - pas * 0.35, y, width: pas * 0.7,
            height: Math.max(1, Math.abs(Y(b.o) - Y(b.c))), fill: col }, gc);
        }
      }

      //  --- les ordres, poses sur le chandelier ou ils ont eu lieu ---
      this.parK = {};
      const go = el("g", {}, this.svg);
      const eMax = Math.max(1, ...this.o.ordres.map(o => o.euros || 0));
      for (const o of this.o.ordres) {
        const k = kDe(o.ts);
        if (k < 0 || k >= vis.length) continue;
        const x = X(k), y = Y(o.prix);
        if (y < MT - 4 || y > MT + hPrix + 4) continue;
        const t = 4 + 3.5 * Math.sqrt((o.euros || 0) / eMax);
        const achat = o.genre === "achat";
        el("path", {
          d: achat ? `M${x} ${y + t}L${x + t} ${y - t * .6}L${x - t} ${y - t * .6}Z`
                   : `M${x} ${y - t}L${x + t} ${y + t * .6}L${x - t} ${y + t * .6}Z`,
          fill: achat ? "var(--achat)" : "var(--vente)",
          stroke: "var(--surface)", "stroke-width": 0.8 }, go);
        (this.parK[k] || (this.parK[k] = [])).push(o);
      }

      //  --- dernier prix, sur l'axe : le reperage le plus utilise ---
      const der = vis[vis.length - 1];
      el("line", { x1: 0, x2: iw, y1: Y(der.c), y2: Y(der.c), stroke: "var(--ink-3)",
        "stroke-width": 1, "stroke-dasharray": "4 3", opacity: 0.8 }, this.svg);
      el("rect", { x: iw + 1, y: Y(der.c) - 9, width: MR - 2, height: 18, rx: 2,
        fill: der.c >= der.o ? "var(--hausse)" : "var(--baisse)" }, this.svg);
      el("text", { x: iw + 6, y: Y(der.c) + 4, fill: "var(--sur-vif)", "font-size": 11,
        "font-weight": 600, "font-family": '"IBM Plex Mono",monospace' }, this.svg)
        .textContent = prixCourt(der.c);

      this.gCroix = el("g", { "pointer-events": "none" }, this.svg);
    }

    croix(evt) {
      const g = this.geo;
      if (!g || !this.gCroix) return;
      const r = this.svg.getBoundingClientRect();
      const x = evt.clientX - r.left, y = evt.clientY - r.top;
      this.gCroix.replaceChildren();
      if (x < 0 || x > g.iw || y < 0 || y > g.H) { this.cacher(); return; }
      const k = Math.max(0, Math.min(g.vis.length - 1, Math.floor(x / g.pas)));
      const b = g.vis[k];
      el("line", { x1: g.X(k), x2: g.X(k), y1: g.MT, y2: g.H - g.MB,
        stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "3 3" }, this.gCroix);
      el("line", { x1: 0, x2: g.iw, y1: y, y2: y,
        stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "3 3" }, this.gCroix);
      if (y >= g.MT && y <= g.MT + g.hPrix) {
        const prix = g.lo + (1 - (y - g.MT) / g.hPrix) * (g.hi - g.lo);
        el("rect", { x: g.iw + 1, y: y - 9, width: g.MR - 2, height: 18, rx: 2,
          fill: "var(--ink)" }, this.gCroix);
        el("text", { x: g.iw + 6, y: y + 4, fill: "var(--sur-vif)", "font-size": 11,
          "font-family": '"IBM Plex Mono",monospace' }, this.gCroix)
          .textContent = prixCourt(prix);
      }
      const pc = (b.c / b.o - 1);
      let html = `<b>${jjh(b.ts)}</b>`
        + `<span>O <i>${prixCourt(b.o)}</i></span>`
        + `<span>H <i>${prixCourt(b.h)}</i></span>`
        + `<span>B <i>${prixCourt(b.l)}</i></span>`
        + `<span>C <i class="${pc >= 0 ? "h" : "b"}">${prixCourt(b.c)} `
        + `(${pc >= 0 ? "+" : ""}${(pc * 100).toFixed(2)} %)</i></span>`;
      for (const o of (this.parK[k] || []))
        html += `<span class="ordre ${o.genre}">${o.genre} ${prixCourt(o.prix)} · `
          + `${(o.euros || 0).toFixed(2)} €`
          + (o.gain != null ? ` · gain ${o.gain >= 0 ? "+" : ""}${o.gain.toFixed(2)} €` : "")
          + `</span>`;
      this.info.innerHTML = html;
      this.info.hidden = false;
      //  l'infobulle bascule a gauche quand le curseur approche du bord droit,
      //  sinon elle sortirait de la carte
      const large = this.info.offsetWidth || 150;
      this.info.style.left = (x + 14 + large > g.W ? x - large - 14 : x + 14) + "px";
      this.info.style.top = Math.max(4, Math.min(g.H - 90, y - 10)) + "px";
    }

    cacher() {
      if (this.gCroix) this.gCroix.replaceChildren();
      this.info.hidden = true;
    }
  }

  Marche.prixCourt = prixCourt;
  Marche.ticks = ticks;
  global.Marche = Marche;
})(window);
