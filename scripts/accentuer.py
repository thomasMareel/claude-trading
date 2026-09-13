"""Remet les accents dans le texte visible des pages, sans toucher au code.

    python scripts/accentuer.py --verifier     # ne modifie rien, montre ce qui changerait
    python scripts/accentuer.py                # applique

POURQUOI. Les pages ont ete ecrites sans accents, parce que les longues chaines
passees au shell les perdaient. Sur un site francais c'est un defaut qu'on voit
avant tout le reste : « la methode », « le reglage », « les donnees ».

POURQUOI C'EST DELICAT, et comment ce script s'y prend. Les memes mots servent
d'identifiants : methode.html, class="etiquette", .legende span, E.fenetre,
q.set("periode"). Une premiere version remplacait partout apres avoir masque
quelques motifs ; elle a transforme « .legende span{ » en « .légende span{ » et
aurait casse la feuille de style sans que rien ne le signale. Le decoupage est
donc maintenant STRUCTUREL, et non par expression reguliere sur le tout :

  <style>   jamais touche. Aucun texte visible n'y vit, et tout y est selecteur.
  <script>  accentue UNIQUEMENT a l'interieur des chaines de caracteres qui
            contiennent une espace — donc de la prose, jamais un identifiant, une
            classe ni une cle. Les attributs techniques et les chemins de
            fichiers sont en plus masques a l'interieur de ces chaines.
  le reste  le texte entre les balises, plus les attributs qui s'affichent :
            title, aria-label, alt, placeholder, et le content des meta.

VERIFICATION. Avant d'ecrire, le script compare la liste des class, id, href,
src, des variables CSS, des noms de fichiers et des selecteurs CSS : si un seul
a bouge, il refuse le fichier.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accents_mots import SUPPLEMENT  # noqa: E402

CIBLES = ["docs/index.html", "docs/methode.html", "docs/paper.html",
          "docs/journal.html", "docs/validation.html",
          "docs/simulations.template.html", "docs/simulations.html",
          "docs/nav.js", "docs/marche.js"]

#  Le vocabulaire du site. Seuls des mots dont l'accent ne fait aucun doute.
MOTS = {
    "apres": "après", "deja": "déjà",
    "methode": "méthode", "methodes": "méthodes",
    "reglage": "réglage", "reglages": "réglages", "regle": "règle", "regles": "règles",
    "reglee": "réglée", "reglees": "réglées", "regler": "régler",
    "donnees": "données", "donnee": "donnée",
    "echelle": "échelle", "echelles": "échelles",
    "periode": "période", "periodes": "périodes",
    "resultat": "résultat", "resultats": "résultats",
 "mesurees": "mesurées",     "precedent": "précédent", "precedents": "précédents", "precedente": "précédente",
    "precedentes": "précédentes", "precede": "précède",
    "realise": "réalise", "realisee": "réalisée", "realises": "réalisés",
    "reference": "référence", "references": "références",
    "repere": "repère", "reperes": "repères", "reperage": "repérage",
    "etude": "étude", "etudes": "études", "etudier": "étudier", "etudie": "étudie",
    "etat": "état", "etats": "états",
    "etiquette": "étiquette", "etiquettes": "étiquettes", "etiquetage": "étiquetage",
    "etale": "étale", "etaler": "étaler", "etalee": "étalée",
    "ecart": "écart", "ecarts": "écarts", "ecarte": "écarté", "ecartee": "écartée",
    "ecartes": "écartés", "ecartees": "écartées", "ecarter": "écarter",
 "ecrite": "écrite", "ecrits": "écrits",     "ecrire": "écrire", "ecriture": "écriture",
    "eliminer": "éliminer",
    "eprouve": "éprouve", "eprouvee": "éprouvée", "epreuve": "épreuve",
    "epreuves": "épreuves",
    "evenement": "événement", "evenements": "événements",
    "execute": "exécute", "executee": "exécutée", "executes": "exécutés",
    "executable": "exécutable", "executables": "exécutables", "execution": "exécution",
    "frequent": "fréquent", "frequente": "fréquente", "frequence": "fréquence",
    "generale": "générale", "genere": "génère", "generee": "générée",
    "geometrie": "géométrie", "geometrique": "géométrique",
    "immobilise": "immobilise", "immobilisee": "immobilisée",
    "interet": "intérêt", "interets": "intérêts",
    "invalide": "invalide", "invalidee": "invalidée",
    "liquidite": "liquidité",
    "mecanique": "mécanique", "mecanisme": "mécanisme",
    "mediane": "médiane", "medianes": "médianes",
    "publie": "publie", "publiee": "publiée", "publies": "publiés",
    "publiees": "publiées",
    "realite": "réalité", "recuperer": "récupérer",
 "rejouee": "rejouée", "rejoues": "rejoués",
    "rejouees": "rejouées",
    "remede": "remède",
    "repartition": "répartition", "reparti": "réparti", "repartis": "répartis",
    "repartie": "répartie", "repartir": "répartir",
    "represente": "représente", "representent": "représentent",
    "resserre": "resserre", "resserres": "resserrés", "resserree": "resserrée",
    "securite": "sécurité", "selection": "sélection", "selectionne": "sélectionne",
    "serie": "série", "series": "séries",
    "stabilite": "stabilité", "strategie": "stratégie", "strategies": "stratégies",
    "telecharge": "télécharge", "telechargement": "téléchargement",
    "telecharger": "télécharger", "telechargees": "téléchargées",
    "verifie": "vérifie", "verifiee": "vérifiée", "verifier": "vérifier",
    "verification": "vérification", "verifiable": "vérifiable",
    "volatilite": "volatilité",
    "decoupe": "découpe", "decoupee": "découpée", "decoupage": "découpage",
    "decouper": "découper", "decoupes": "découpés",
    "declenche": "déclenche", "declenchee": "déclenchée", "declencher": "déclencher",
    "decide": "décide", "decident": "décident", "decider": "décider",
    "decision": "décision", "decisions": "décisions",
    "defaut": "défaut", "defaillance": "défaillance",
    "demonstration": "démonstration", "demontre": "démontre",
    "depart": "départ", "departs": "départs",
    "depasse": "dépasse", "depassent": "dépassent",
    "deplace": "déplace", "deplacement": "déplacement",
    "desormais": "désormais", "detail": "détail", "details": "détails",
    "detenir": "détenir", "detenu": "détenu",  "detient": "détient",
    "determine": "détermine",
    "different": "différent", "differents": "différents", "differente": "différente",
    "differentes": "différentes", "difference": "différence", "differences": "différences",
    "ete": "été", "etre": "être",
    "meme": "même", "memes": "mêmes",
    "tres": "très",
    "fenetre": "fenêtre", "fenetres": "fenêtres",
    "cout": "coût", "couts": "coûts", "coute": "coûte", "coutait": "coûtait",
    "coutent": "coûtent", "coutaient": "coûtaient", "couter": "coûter",
    "plutot": "plutôt", "cote": "côté", "cotes": "côtés",
    "role": "rôle", "controle": "contrôle", "controles": "contrôles",
    "arrete": "arrête", "arretee": "arrêtée", "arretes": "arrêtés", "arreter": "arrêter",
    "arret": "arrêt", "arrets": "arrêts",
    "interessant": "intéressant", "interessante": "intéressante",
    "deuxieme": "deuxième", "troisieme": "troisième", "quatrieme": "quatrième",
    "cinquieme": "cinquième", "sixieme": "sixième", "septieme": "septième",
    "huitieme": "huitième", "neuvieme": "neuvième", "dixieme": "dixième",
    "premiere": "première", "premieres": "premières",
    "derniere": "dernière", "dernieres": "dernières",
    "maniere": "manière", "manieres": "manières",
    "entiere": "entière", "entieres": "entières",
    "critere": "critère", "criteres": "critères",
    "parametre": "paramètre", "parametres": "paramètres",
    "systematique": "systématique",
    "modele": "modèle", "modeles": "modèles",
    "probleme": "problème", "problemes": "problèmes",
    "systeme": "système", "systemes": "systèmes",
    "theme": "thème", "themes": "thèmes",
    "annee": "année", "annees": "années",
    "journee": "journée",
    "elargi": "élargi", "elargie": "élargie",
    "eleve": "élève", "elevee": "élevée", "eleves": "élevés", "elevation": "élévation",
    "enorme": "énorme",
    "espace": "espace", "espacement": "espacement",
    "evite": "évite", "eviter": "éviter", "evidence": "évidence", "evident": "évident",
    "evidemment": "évidemment",
    "exigee": "exigée", "experience": "expérience", "experiences": "expériences",
    "explique": "explique", "expliquee": "expliquée",
    "immediat": "immédiat", "immediate": "immédiate", "immediatement": "immédiatement",
    "independant": "indépendant", "independants": "indépendants",
    "independante": "indépendante", "independantes": "indépendantes",
    "integralement": "intégralement",
    "interieur": "intérieur",
    "legende": "légende", "legendes": "légendes", "leger": "léger", "legere": "légère",
    "lisibilite": "lisibilité",
    "necessaire": "nécessaire", "necessaires": "nécessaires",
    "negatif": "négatif", "negatifs": "négatifs", "negative": "négative",
    "negatives": "négatives",
    "numero": "numéro", "numeros": "numéros",
    "operation": "opération", "operations": "opérations",
    "particuliere": "particulière",
    "penalise": "pénalise", "penalisee": "pénalisée",
    "precisement": "précisément", "precision": "précision",
    "prefere": "préfère", "preferer": "préférer",
    "prepare": "prépare", "preparer": "préparer",
    "present": "présent", "presente": "présente", "presentee": "présentée",
    "presentes": "présentés", "presenter": "présenter", "presentation": "présentation",
    "prevoir": "prévoir", "prevu": "prévu", "prevue": "prévue", "prevision": "prévision",
    "procede": "procédé", "procedes": "procédés",
    "protege": "protège", "protegee": "protégée", "proteger": "protéger",
    "qualite": "qualité", "quantite": "quantité", "quantites": "quantités",
    "regime": "régime", "regimes": "régimes",
    "regulier": "régulier", "reguliere": "régulière", "regulierement": "régulièrement",
    "reponse": "réponse", "reponses": "réponses", "repond": "répond", "repondre": "répondre",
    "reseau": "réseau",
    "reserve": "réserve", "reservee": "réservée",
    "resume": "résume", "resumee": "résumée", "resumer": "résumer",
    "reveil": "réveil", "reveils": "réveils",
    "signale": "signale", "signalee": "signalée",
    "simplifie": "simplifie",
    "specifique": "spécifique", "specifiques": "spécifiques",
    "succes": "succès",
    "supprime": "supprime", "supprimee": "supprimée", "supprimer": "supprimer",
    "unite": "unité", "unites": "unités",
    "utilise": "utilise", "utilisee": "utilisée", "utilises": "utilisés",
    "utiliser": "utiliser",
    "verite": "vérité", "verites": "vérités",
    "visee": "visée", "visees": "visées",
}

#  « a » ou « à » ne se devine pas : « le reglage qui a le mieux rendu » est le
#  verbe avoir, « de 14 a 384 EUR » est la preposition. Une premiere version
#  accentuait « a » devant tout article et ecrivait « qui à le mieux rendu ». On
#  ne garde donc que des cas OU LA CONFUSION EST IMPOSSIBLE : les locutions
#  figees, et « a » suivi d'un nombre.
#  Le supplement, releve mot par mot dans les pages. Il ecrase le noyau quand les
#  deux se recouvrent : c'est lui qui a ete verifie le plus recemment.
MOTS.update(SUPPLEMENT)
MOTS = {k: v for k, v in MOTS.items() if k != v}

REGLES = [
    (r"\bjusqu'a\b", "jusqu'à"),
    (r"\bau-dela\b", "au-delà"),
    (r"\bvoila\b", "voilà"),
    (r"\bdes que\b", "dès que"),
    (r"\bdes lors\b", "dès lors"),
    (r"\bc'est-a-dire\b", "c'est-à-dire"),
    (r"\bpar-dela\b", "par-delà"),
    (r"\b(face|grace|quant|rapport|contrairement|comparee?s?|identique|oppose)\s+a\b",
     r"\1 à"),
    (r"\ba (partir|nouveau|gauche|droite|travers|cause|peine|present|jamais|moitie|"
     r"cote|savoir|tort|raison|hauteur|propos|defaut|titre|condition|mesure|"
     r"l'avance|l'instant|l'endroit|l'heure|l'inverse|l'identique|l'oeil|l'envers|"
     r"la main|la fois|la hausse|la baisse|la place|la suite|la difference)\b",
     r"à \1"),
    #  « a » devant un nombre, un signe ou un symbole : toujours la preposition.
    (r"\ba (?=[-+]?\d)", "à "),
    (r"\ba (?=[-+]\s*\d)", "à "),
]

#  A l'interieur d'une chaine de prose, ceci reste du code. L'ORDRE COMPTE : les
#  interpolations sont masquees EN PREMIER, sans quoi un attribut qui en contient
#  une — class="entree${...}" — serait coupe au milieu par le masquage suivant,
#  qui mordrait alors sur le texte.
DANS_CHAINE = [
    re.compile(r"""\$\{(?:[^{}]|\{[^{}]*\})*\}"""),
    re.compile(r"""(?:class|id|href|src|for|name|type|rel|role|style|aria-\w+|data-\w+)\s*=\s*(?:\\?"[^"]*\\?"|'[^']*'|[^\s>"']+)"""),
    re.compile(r"""\b[\w-]+\.(?:html|json|js|css|py|bat|md|svg|png)\b"""),
    re.compile(r"""https?://[^\s"'<>)]+"""),
    re.compile(r"""var\(\s*--[^)]*\)"""),
    re.compile(r"""--[a-z0-9-]+"""),
]

#  Les chaines passees a une fonction qui attend un identifiant ne sont jamais du
#  texte : on les met de cote en entier, ou qu'elles se trouvent.
APPELS = re.compile(
    r"""(?:getElementById|querySelectorAll|querySelector|getAttribute|setAttribute|"""
    r"""removeAttribute|toggleAttribute|hasAttribute|createElementNS|createElement|"""
    r"""classList\.\w+|addEventListener|removeEventListener|getPropertyValue|"""
    r"""setProperty|getItem|setItem|removeItem|URLSearchParams)\s*\("""
    r"""\s*(?:"[^"]*"|'[^']*')""")


def accentuer_texte(s: str) -> str:
    def mot(m):
        w = m.group(0)
        a = MOTS.get(w.lower())
        if not a:
            return w
        return a[0].upper() + a[1:] if w[0].isupper() else a
    s = re.sub(r"\b[A-Za-zÀ-ÿ']+\b", mot, s)
    for rx, rep in REGLES:
        s = re.sub(rx, rep, s)
    return s


def accentuer_prose(s: str) -> str:
    """Accentue en preservant ce qui, a l'interieur, reste du code."""
    garde: list[str] = []
    def mettre(m):
        garde.append(m.group(0))
        return f"\x00{len(garde)-1}\x00"
    for rx in DANS_CHAINE:
        s = rx.sub(mettre, s)
    s = accentuer_texte(s)
    #  Les masques s'emboitent — un class="..." avale un ${...} deja masque — et
    #  re.sub ne repasse pas sur ce qu'il vient d'ecrire. On remet donc en place
    #  jusqu'a ce qu'il ne reste plus un seul jeton, sans quoi ils se retrouvent
    #  dans la page. C'est exactement ce qui est arrive a la premiere version.
    for _ in range(8):
        neuf = re.sub("\x00(\\d+)\x00", lambda m: garde[int(m.group(1))], s)
        if neuf == s:
            break
        s = neuf
    return s


CHAINE = re.compile(r"""(`(?:[^`\\]|\\.)*`|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")


def traiter_script(s: str) -> str:
    """Dans du JavaScript, n'accentue que les chaines qui portent du texte."""
    garde: list[str] = []

    def mettre(m):
        garde.append(m.group(0))
        return "\x01%d\x01" % (len(garde) - 1)

    s = APPELS.sub(mettre, s)

    def une(m):
        c = m.group(0)
        dedans = c[1:-1]
        #  De la prose : elle contient une espace. Ou bien un mot du vocabulaire
        #  employe seul comme etiquette — « echelle », « periode ». Tout le reste
        #  est un identifiant, une classe, une cle ou un selecteur.
        #  Un mot seul n'est PAS accentue, meme s'il figure au vocabulaire : rien
        #  ne le distingue d'un nom de classe ou d'une cle. Une premiere version
        #  le faisait et a transforme h("div", {class:"donnees"}) en class
        #  "données", ce qui casse la feuille de style en silence. Les quelques
        #  etiquettes d'un seul mot sont accentuees a la main dans la source.
        if " " in dedans and len(dedans) >= 4:
            return c[0] + accentuer_prose(dedans) + c[-1]
        return c

    s = CHAINE.sub(une, s)
    return re.sub("\x01(\\d+)\x01", lambda m: garde[int(m.group(1))], s)


BLOC = re.compile(r"(<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>)",
                  re.S | re.I)
ATTR_VISIBLE = re.compile(
    r"""((?:title|aria-label|alt|placeholder|content)\s*=\s*")([^"]*)(")""", re.I)
BALISE = re.compile(r"(<[^>]*>)")


def traiter_html(s: str) -> str:
    """Hors style et script : le texte entre balises, plus les attributs visibles."""
    out = []
    for part in BALISE.split(s):
        if part.startswith("<"):
            out.append(ATTR_VISIBLE.sub(
                lambda m: m.group(1) + accentuer_texte(m.group(2)) + m.group(3), part))
        else:
            out.append(accentuer_texte(part))
    return "".join(out)


def traiter(s: str, est_js: bool) -> str:
    if est_js:
        return traiter_script(s)
    morceaux = BLOC.split(s)
    for i, p in enumerate(morceaux):
        bas = p[:8].lower()
        if bas.startswith("<style"):
            continue                                   # jamais touche
        if bas.startswith("<script"):
            morceaux[i] = traiter_script(p)
        else:
            morceaux[i] = traiter_html(p)
    return "".join(morceaux)


def signature(s: str) -> tuple:
    """Ce qui ne doit surtout pas bouger."""
    return (
        tuple(re.findall(r"""(?:class|id|href|src)\s*=\s*"([^"]*)\"""", s)),
        tuple(sorted(set(re.findall(r"--[a-z0-9-]+", s)))),
        tuple(re.findall(r"\b[\w-]+\.(?:html|json|js|css)\b", s)),
        #  tous les selecteurs CSS et les cles JS : un accent y serait fatal
        tuple(re.findall(r"[.#][A-Za-z][\w-]*", s)),
        tuple(re.findall(r"\bfunction\s+(\w+)", s)),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args()
    total, refus = 0, 0
    for cible in CIBLES:
        c = RACINE / cible
        if not c.exists():
            print(f"  {cible:<34} absent")
            continue
        avant = c.read_text(encoding="utf-8")
        apres = traiter(avant, cible.endswith(".js"))
        acc = lambda x: len(re.findall(r"[àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", x))  # noqa: E731
        diff = acc(apres) - acc(avant)
        a, b = signature(avant), signature(apres)
        if a != b:
            noms = ("class/id/href/src", "variables CSS", "noms de fichiers",
                    "selecteurs", "fonctions")
            for i, (x, y) in enumerate(zip(a, b)):
                if x == y:
                    continue
                print(f"  {cible:<34} REFUSE : {noms[i]} — "
                      f"disparus {sorted(set(x) - set(y))[:4]}, "
                      f"apparus {sorted(set(y) - set(x))[:4]}")
            refus += 1
            continue
        total += diff
        if args.verifier:
            print(f"  {cible:<34} +{diff} accents")
            continue
        c.write_text(apres, encoding="utf-8")
        print(f"  {cible:<34} +{diff} accents, ecrit")
    print(f"\n  {total} accents au total" + (f", {refus} fichier(s) refuse(s)" if refus else ""))
    return 1 if refus else 0


if __name__ == "__main__":
    raise SystemExit(main())
