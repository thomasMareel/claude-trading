/*  Les préférences d'affichage du visiteur, en un seul endroit.
 *
 *  CE FICHIER SE CHARGE DE FAÇON SYNCHRONE, DANS <head>, sans defer. C'est
 *  volontaire et c'est la seule façon d'éviter le clignotement : nav.js est en
 *  defer, donc tout thème appliqué depuis nav.js arriverait APRÈS le premier
 *  rendu et la page passerait par le clair à chaque chargement avant de virer
 *  au sombre. Le tampon doit précéder la peinture.
 *
 *  La recommandation classique est d'écrire ces huit lignes en ligne dans
 *  chaque <head> pour économiser l'aller-retour. Ce dépôt a déjà payé deux fois
 *  le prix d'une même vérité recopiée dans plusieurs pages — les couleurs
 *  divergeaient, la liste des pages divergeait. Un fichier de quatre kilooctets,
 *  mis en cache dès la deuxième page, coûte moins cher qu'une septième copie
 *  qu'on oubliera de mettre à jour.
 *
 *  CE QUI EST ICI ET CE QUI N'Y EST PAS. Ici : ce que le LECTEUR préfère, sur
 *  cet appareil, pour toutes les pages — thème, densité, forme des marques.
 *  Pas ici : ce qu'il REGARDE — robot, paire, période, unité, calques, cycle.
 *  Cet état-là vit dans l'adresse, parce qu'il se partage, et un lien qui rouvre
 *  exactement la même vue vaut mieux qu'une préférence enfermée dans un
 *  navigateur. Règle écrite et sans exception : au chargement, l'URL l'emporte.
 */
(function (global) {
  "use strict";

  const CLE = "banc.prefs.v1";

  /*  Un seul objet, une seule clé. Les valeurs sont des mots et non des
   *  booléens ou des nombres : « compacte » se relit dans le stockage, « 0.85 »
   *  non — et le jour où l'échelle change, les préférences enregistrées ne
   *  deviennent pas fausses.  */
  const DEFAUTS = {
    theme: "auto",          // auto | light | dark
    densite: "large",       // large | compacte
    mouvement: "auto",      // auto | reduit
    fuseau: "utc",          // utc | local
    decimales: "deux",      // deux | paire
    marques: "vert-rouge",  // vert-rouge | bleu-orange
    forme: "triangles",     // triangles | rond-losange | barres
    taille: "normale",      // discrete | normale | grande
    proportion: "mise",     // mise | egale
    hauteur: "normal",      // compact | normal | grand
    reticule: "aimante",    // aimante | libre
  };

  /*  Les seuls réglages qui se tamponnent sur <html> : ceux que la CSS lit
   *  seule. Les autres sont lus par le graphique, en JavaScript, au dessin.
   *  « auto » ne s'écrit pas — un attribut absent est l'état par défaut, et
   *  écrire data-theme="auto" obligerait chaque sélecteur à le prévoir.  */
  const TAMPONS = { theme: "theme", densite: "densite",
                    mouvement: "mouvement", marques: "marques" };
  const MUETS = { theme: "auto", densite: "large",
                  mouvement: "auto", marques: "vert-rouge" };

  let etat = null;
  const abonnes = [];

  function lireStockage() {
    //  La navigation privée de Safari lève à la lecture comme à l'écriture.
    try {
      const brut = global.localStorage.getItem(CLE);
      if (!brut) return {};
      const o = JSON.parse(brut);
      return (o && typeof o === "object") ? o : {};
    } catch (e) { return {}; }
  }

  function ecrireStockage(o) {
    try {
      //  On n'enregistre que ce qui s'écarte du défaut. Le stockage reste
      //  lisible, et un défaut qui changerait un jour s'appliquerait aux
      //  visiteurs qui ne l'avaient jamais touché — ce qu'on veut.
      const maigre = {};
      for (const k in DEFAUTS) if (o[k] !== DEFAUTS[k]) maigre[k] = o[k];
      if (Object.keys(maigre).length) global.localStorage.setItem(CLE, JSON.stringify(maigre));
      else global.localStorage.removeItem(CLE);
    } catch (e) { /* rien à faire, et rien à dire au visiteur */ }
  }

  function normaliser(o) {
    const out = Object.assign({}, DEFAUTS);
    for (const k in DEFAUTS)
      if (typeof o[k] === "string" && o[k]) out[k] = o[k];
    return out;
  }

  function tamponner() {
    const r = document.documentElement;
    for (const k in TAMPONS) {
      const v = etat[k];
      if (!v || v === MUETS[k]) delete r.dataset[TAMPONS[k]];
      else r.dataset[TAMPONS[k]] = v;
    }
  }

  /*  La couleur de la barre d'adresse sur mobile. Sans elle, un site sombre
   *  garde un bandeau clair en haut de l'écran, ce qui se voit tout de suite.
   *  On la relit sur la page plutôt que de la recopier : c'est --ground qui
   *  fait autorité, et elle change avec le thème.  */
  function majBarreSysteme() {
    let m = document.querySelector('meta[name="theme-color"]');
    if (!m) {
      m = document.createElement("meta");
      m.name = "theme-color";
      (document.head || document.documentElement).appendChild(m);
    }
    //  getPropertyValue rend la valeur calculée, donc light-dark() déjà résolu.
    const c = getComputedStyle(document.documentElement)
      .getPropertyValue("--ground").trim();
    if (c) m.setAttribute("content", c);
  }

  function prevenir(cle) {
    for (const f of abonnes) { try { f(etat, cle); } catch (e) { console.error(e); } }
  }

  etat = normaliser(lireStockage());
  tamponner();

  const Prefs = {
    DEFAUTS: DEFAUTS,
    tout() { return Object.assign({}, etat); },
    get(k) { return etat[k]; },
    /*  vrai si la valeur a changé — le panneau s'en sert pour ne pas redessiner
     *  un graphique quand on reclique l'option déjà cochée.  */
    set(k, v) {
      if (!(k in DEFAUTS) || etat[k] === v) return false;
      etat[k] = v;
      ecrireStockage(etat);
      tamponner();
      majBarreSysteme();
      prevenir(k);
      return true;
    },
    raz() {
      try { global.localStorage.removeItem(CLE); } catch (e) {}
      etat = Object.assign({}, DEFAUTS);
      tamponner();
      majBarreSysteme();
      prevenir(null);
    },
    /*  S'abonner rend la fonction de désabonnement. Personne ne s'en sert
     *  encore ; le jour où un graphique est détruit, il en aura besoin.  */
    sur(f) {
      abonnes.push(f);
      return () => { const i = abonnes.indexOf(f); if (i >= 0) abonnes.splice(i, 1); };
    },
    majBarreSysteme: majBarreSysteme,
  };

  /*  Les valeurs dont le graphique a besoin, traduites une bonne fois : le
   *  dessin ne doit pas connaître le vocabulaire des préférences.  */
  Prefs.graphique = function () {
    const t = { discrete: 0.8, normale: 1, grande: 1.3 }[etat.taille] || 1;
    //  La hauteur est un FACTEUR et non une valeur : chaque page a choisi la
    //  sienne pour de bonnes raisons — la planche d'un cycle n'a pas besoin de
    //  la meme place que le graphique principal du direct. Une valeur absolue
    //  ecraserait ces choix ; un facteur les respecte tous les trois.
    const h = { compact: 0.82, normal: 1, grand: 1.35 }[etat.hauteur] || 1;
    return {
      forme: etat.forme, tailleMarque: t, proportionnel: etat.proportion === "mise",
      echelleHauteur: h, aimante: etat.reticule !== "libre",
      utc: etat.fuseau !== "local", decimalesPaire: etat.decimales === "paire",
    };
  };

  global.Prefs = Prefs;

  //  La barre système ne peut se lire qu'une fois la feuille de style arrivée.
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", majBarreSysteme);
  else majBarreSysteme();
})(window);
