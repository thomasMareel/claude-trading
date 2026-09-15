"""Le compte rendu : une page imprimable, schematisee, chiffres a jour.

    python scripts/rapport.py                 # HTML puis PDF
    python scripts/rapport.py --sans-pdf      # HTML seulement

POURQUOI UN GENERATEUR PLUTOT QU'UN DOCUMENT. Un compte rendu ecrit a la main
commence juste et vieillit faux : les chiffres y sont recopies, et la premiere
mesure refaite les demente en silence. Ici, la prose est ecrite une fois et
TOUS LES NOMBRES sont relus dans les fichiers de mesure a chaque generation. Un
nombre qui manque laisse un trou visible, jamais une valeur perimee.

Le PDF est imprime par le navigateur du systeme, en mode sans fenetre. C'est la
seule dependance, et elle est deja installee sur toute machine.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SORTIE = RACINE / "rapport"

NAVIGATEURS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def lire(chemin: str):
    p = RACINE / chemin
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def court(s):
    return s.split("/")[0]


def pc(x, n=2, signe=True):
    if x is None:
        return "—"
    return ("+" if signe and x >= 0 else "") + f"{x * 100:.{n}f} %"


def eur(x):
    """Un montant en euros, ecrit comme on l'ecrit en francais.

    Les milliers sont separes par une espace fine insecable, et seuls les
    millions passent en abrege : « 1 000 EUR » se lit, « 1.0 k EUR » se decode.
    """
    if x is None:
        return "—"
    if x >= 1e6:
        return f"{x / 1e6:.1f} M€".replace(".", ",")
    return f"{x:,.0f} €".replace(",", " ")


# ---------------------------------------------------------------- schemas
def schema_chaine(etapes: dict) -> str:
    """Le chemin d'une bougie jusqu'a un verdict. Cinq etages, pas un de plus.

    Un schema de chaine ne sert que s'il montre OU une erreur peut entrer. Les
    deux etages du haut ne rejouent rien : c'est pour cela qu'ils sont a part,
    et c'est pour cela que leur resultat ne peut pas etre surajuste.
    """
    boites = [
        ("Les prix", "880 jours · 5 min · 10 paires<br>253 440 bougies par paire", "src"),
        ("Sans aucun rejeu", "volatilite · bougies plates · derive<br>volume et taille servable",
         "mes"),
        ("Le balayage", "<b>%s</b><br>22 tranches × 2 budgets" % etapes.get("total", "—"), "bal"),
        ("La lecture", "choix en avant · classes<br>temoin, hasard, ne rien faire", "lec"),
        ("Ce qui est publie", "page d'etude · ce compte rendu", "pub"),
    ]
    L, H, G = 152, 74, 22
    larg = len(boites) * L + (len(boites) - 1) * G
    out = [f'<div class="defile"><svg class="sch" viewBox="0 0 {larg} {H + 30}" '
           f'style="min-width:{larg // 2}px" role="img" '
           f'aria-label="La chaine de mesure, cinq etages">']
    for i, (titre, sous, cle) in enumerate(boites):
        x = i * (L + G)
        cls = "b-mes" if cle == "mes" else ("b-bal" if cle == "bal" else "b-def")
        out.append(f'<rect class="{cls}" x="{x}" y="14" width="{L}" height="{H}" rx="8"/>')
        out.append(f'<text class="t-t" x="{x + 12}" y="36">{titre}</text>')
        out.append(f'<foreignObject x="{x + 12}" y="42" width="{L - 24}" height="{H - 30}">'
                   f'<div xmlns="http://www.w3.org/1999/xhtml" class="t-s">{sous}</div>'
                   f'</foreignObject>')
        if i < len(boites) - 1:
            fx = x + L
            out.append(f'<path class="fl" d="M{fx + 3} {14 + H / 2} L{fx + G - 5} {14 + H / 2}"/>')
            out.append(f'<path class="fl-t" d="M{fx + G - 9} {14 + H / 2 - 4} '
                       f'L{fx + G - 4} {14 + H / 2} L{fx + G - 9} {14 + H / 2 + 4}z"/>')
    out.append("</svg></div>")
    return "".join(out)


def schema_entonnoir(ex: dict) -> str:
    """Combien de paires survivent a chaque contrainte d'execution.

    L'entonnoir est la bonne forme ici parce que chaque etage est un SOUS-ENSEMBLE
    du precedent : ce n'est pas un classement, c'est une elimination.
    """
    if not ex:
        return '<p class="trou">mesure d\'execution absente</p>'
    paires = ex["paires"]
    budgets = sorted(ex["mises"], key=float)
    bas = {b: ex["mises"][b][-1] for b in budgets}
    etages = [("Le panier de liquidite", list(paires), "les dix paires retenues sur OKX Europe")]
    for b in budgets:
        gard = [s for s in paires if bas[b] <= paires[s]["servable"]]
        etages.append((f"Executable a {eur(float(b))} par paire", gard,
                       f"barreau du bas : {eur(bas[b])}"))
    L, H, G = 560, 52, 16
    out = [f'<svg class="sch" viewBox="0 0 {L} {len(etages) * (H + G)}" role="img" '
           f'aria-label="Entonnoir de l\'executabilite">']
    for i, (titre, lot, note) in enumerate(etages):
        y = i * (H + G)
        w = L * (0.34 + 0.66 * len(lot) / len(paires))
        x = (L - w) / 2
        cls = "b-ok" if len(lot) == len(paires) else ("b-ko" if len(lot) <= 2 else "b-mi")
        out.append(f'<rect class="{cls}" x="{x:.0f}" y="{y}" width="{w:.0f}" height="{H}" rx="7"/>')
        out.append(f'<text class="t-t" x="{L / 2}" y="{y + 20}" text-anchor="middle">'
                   f'{titre} — {len(lot)}/{len(paires)}</text>')
        out.append(f'<text class="t-s2" x="{L / 2}" y="{y + 36}" text-anchor="middle">'
                   f'{", ".join(court(s) for s in lot) if lot else "aucune"}</text>')
        out.append(f'<text class="t-s3" x="{L / 2}" y="{y + 47}" text-anchor="middle">'
                   f'{note}</text>')
    out.append("</svg>")
    return "".join(out)


def schema_moteur(champs: list[tuple[str, str, str]]) -> str:
    """Ce qui a ete ajoute au moteur, et ce qui le rend sur.

    Chaque ligne porte SA preuve d'inertie : c'est la seule colonne qui compte,
    parce qu'un champ neuf non inerte rend faux tout ce que le depot a deja
    mesure.
    """
    lignes = "".join(
        f'<tr><td class="mono">{a}</td><td>{b}</td><td class="pr">{c}</td></tr>'
        for a, b, c in champs)
    return (f'<div class="defile"><table class="tb"><thead><tr><th>champ</th>'
            f'<th>ce qu\'il fait</th><th>inerte par defaut</th></tr></thead>'
            f'<tbody>{lignes}</tbody></table></div>')


def tuiles(items) -> str:
    return ('<div class="tuiles">'
            + "".join(f'<div class="tu"><div class="tu-v {k}">{v}</div>'
                      f'<div class="tu-l">{l}</div></div>' for v, l, k in items)
            + "</div>")


def tableau_marche(m: dict, ex: dict) -> str:
    if not m:
        return '<p class="trou">marche non mesure</p>'
    paires = sorted(m, key=lambda s: -m[s]["med1h"])
    lig = []
    for s in paires:
        d = m[s]
        srv = ex["paires"][s]["servable"] if ex and s in ex["paires"] else None
        lig.append(
            f'<tr><td class="mono">{court(s)}</td>'
            f'<td class="n">{d["tranches"]}</td>'
            f'<td class="n">{d["moy5"] * 100:.4f}</td>'
            f'<td class="n">{d["med1h"] * 100:.4f}</td>'
            f'<td class="n">{d["plat5"] * 100:.0f} %</td>'
            f'<td class="n">{d["baissieres"]}/{d["tranches"]}</td>'
            f'<td class="n">{eur(srv) if srv else "—"}</td></tr>')
    return ('<div class="defile"><table class="tb"><thead><tr><th>paire</th><th>tranches</th>'
            '<th>ampl. 5 min</th><th>ampl. 1 h</th><th>plates</th>'
            '<th>en baisse</th><th>ordre servable</th></tr></thead><tbody>'
            + "".join(lig) + "</tbody></table></div>")


def corps_etapes(d: dict, repli: str, dits: dict | None = None) -> str:
    """Les resultats du balayage, lus dans les mesures et jamais recopies.

    Tant qu'une etape manque, on laisse le repli du bilan — un trou visible.
    Un compte rendu qui inventerait une section vide serait pire que muet.
    """
    parts = []
    titres = {1: "Étape 1 — la géométrie en pourcentage fixe",
              2: "Étape 2 — la même géométrie, en unités de volatilité",
              3: "Étape 3 — les mécanismes, un à la fois"}
    for n in (1, 2, 3):
        e = d.get(f"etape{n}")
        if not e:
            continue
        parts.append(f'<div class="saut"></div><h2>{titres[n]}</h2>')
        parts.append(f'<p>{len(e["combinaisons"])} combinaisons, rejouées sur '
                     f'{d["n_blocs"]} tranches de {d["bloc_jours"]} jours, '
                     f'{len(d["paires"])} paires et {len(e["budgets"])} budgets.</p>')
        #  La prose d'interpretation vient du bilan, les nombres des mesures.
        #  Une etape sans prose affiche ses chiffres et se tait, ce qui est
        #  preferable a une conclusion ecrite avant de les avoir vus.
        if dits and str(n) in dits:
            parts.append(dits[str(n)])
        for b in sorted(e["budget"], key=float):
            x = e["budget"][b]
            parts.append(f"<h3>Budget {eur(float(b))} par paire</h3>")
            #  Les lignes du verdict viennent du compte rendu texte, donc de la
            #  meme fonction de decision : le PDF ne peut pas dire autre chose
            #  que le script.
            lig = []
            for r in x["avant"].get("reperes", []):
                gras = r["cle"] == "choix" or r["cle"].startswith("classe")
                nom = f"<b>{r['nom']}</b>" if gras else r["nom"]
                lig.append(f'<tr><td>{nom}</td>'
                           f'<td class="n">{pc(r["moyenne"])}</td>'
                           f'<td class="n">{pc(r["mediane"])}</td>'
                           f'<td class="n">{r["part_positive"] * 100:.0f} %</td></tr>')
            if lig:
                parts.append(
                    '<div class="defile"><table class="tb"><thead><tr><th>repère</th>'
                    '<th>moyenne</th><th>médiane</th><th>part &gt; 0</th></tr></thead>'
                    '<tbody>' + "".join(lig) + "</tbody></table></div>")
            nu = x.get("nuage") or []
            s48 = [p for p in nu if p[1] < 48]
            g48 = [p for p in s48 if p[2] > 0]
            if nu:
                meil = max(nu, key=lambda p: p[2])
                parts.append(tuiles([
                    (f"{len(s48)}/{len(nu)}", "combinaisons dont la durée médiane "
                     "tient sous 48 h", "nu" if s48 else "ko"),
                    (f"{len(g48)}", "parmi elles, celles qui rendent positif",
                     "ok" if g48 else "ko"),
                    (pc(meil[2]), f"le meilleur après coup — {meil[3]} paliers, "
                     f"r={meil[4]}", "nu"),
                    (f"{meil[1]:.1f} h", "sa durée de cycle médiane",
                     "ok" if meil[1] < 48 else "ko"),
                ]))
    return "".join(parts) or repli


# ---------------------------------------------------------------- page
STYLE = """
@page{size:A4; margin:14mm 13mm 16mm}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact; print-color-adjust:exact}
body{margin:0; font-family:"IBM Plex Sans",system-ui,"Segoe UI",sans-serif;
  font-size:9.6pt; line-height:1.5; color:#15211f; background:#fff}
h1{font-family:Newsreader,Georgia,serif; font-size:26pt; line-height:1.06;
  font-weight:500; margin:0 0 4mm; letter-spacing:-.02em}
h1 em{font-style:italic; color:#0d6f63}
h2{font-family:Newsreader,Georgia,serif; font-size:15pt; font-weight:500;
  margin:9mm 0 2mm; padding-top:2.5mm; border-top:1px solid #d6ded9;
  position:relative; break-after:avoid; letter-spacing:-.01em}
h2::before{content:""; position:absolute; top:-1px; left:0; width:34px; height:2px;
  background:#0d6f63}
h3{font-size:10pt; font-weight:600; margin:5mm 0 1mm; break-after:avoid}
p{margin:2mm 0; max-width:62em}
.oeil{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:7pt;
  letter-spacing:.16em; text-transform:uppercase; color:#6c7b77; margin:0 0 2mm}
.chapo{font-size:11pt; color:#3c4a47; max-width:54em; margin-bottom:6mm}
.mono{font-family:"IBM Plex Mono",ui-monospace,monospace}
.n{font-family:"IBM Plex Mono",ui-monospace,monospace; font-variant-numeric:tabular-nums;
  text-align:right}
.trou{color:#b4442f; font-family:"IBM Plex Mono",monospace; font-size:8pt;
  border:1px dashed #e0b6ac; padding:3mm; border-radius:3px}
.dit{border-left:2px solid #0d6f63; padding:1mm 0 1mm 4mm; margin:4mm 0; color:#3c4a47;
  break-inside:avoid}
.att{border-left:2px solid #b4442f; padding:1mm 0 1mm 4mm; margin:4mm 0; color:#3c4a47;
  break-inside:avoid}

.tb{border-collapse:collapse; width:100%; font-size:8.4pt; margin:3mm 0;
  break-inside:avoid}
.tb th{font-family:"IBM Plex Mono",monospace; font-size:6.6pt; letter-spacing:.07em;
  text-transform:uppercase; color:#6c7b77; text-align:right; font-weight:500;
  border-bottom:1px solid #b9c5c0; padding:1.4mm 2mm}
.tb td{padding:1.4mm 2mm; border-bottom:1px solid #e6ebe8; text-align:right}
.tb th:first-child,.tb td:first-child{text-align:left}
.tb td.pr{text-align:left; color:#2f6b4f; font-size:7.8pt}
.tb tbody tr:nth-child(even){background:#f6f8f7}

/*  Le document vise l'A4, mais il est relu a l'ecran, parfois etroit. Les trois
    seules choses qui debordaient : les tuiles a quatre colonnes fixes, les
    tableaux a sept colonnes, et les schemas SVG. A l'impression, la largeur est
    toujours celle d'une page A4 et les quatre colonnes reviennent.  */
.tuiles{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:3mm; margin:4mm 0; break-inside:avoid}
@media print{.tuiles{grid-template-columns:repeat(4,1fr)}}
.defile{overflow-x:auto; -webkit-overflow-scrolling:touch}
@media print{.defile{overflow-x:visible}}
.sch{max-width:100%}
.tu{border:1px solid #d6ded9; border-radius:4px; padding:3mm; background:#f9fbfa}
.tu-v{font-family:"IBM Plex Mono",monospace; font-size:15pt; font-weight:500;
  line-height:1.1; font-variant-numeric:tabular-nums}
.tu-v.ko{color:#b4442f} .tu-v.ok{color:#2f6b4f} .tu-v.nu{color:#15211f}
.tu-l{font-size:7.4pt; color:#6c7b77; margin-top:1.5mm; line-height:1.35}

.sch{width:100%; height:auto; margin:4mm 0; break-inside:avoid}
.sch .b-def{fill:#f2f6f4; stroke:#c6d2cd}
.sch .b-mes{fill:#eaf4f0; stroke:#9fc9ba}
.sch .b-bal{fill:#e7f0fa; stroke:#a8c3de}
.sch .b-ok{fill:#e7f3ec; stroke:#8fbfa3}
.sch .b-mi{fill:#fdf4e6; stroke:#e0c08a}
.sch .b-ko{fill:#fbebe7; stroke:#e0a99c}
.sch .t-t{font-family:"IBM Plex Sans",sans-serif; font-size:8.4px; font-weight:600;
  fill:#15211f}
.sch .t-s2{font-family:"IBM Plex Mono",monospace; font-size:7.6px; fill:#3c4a47}
.sch .t-s3{font-family:"IBM Plex Sans",sans-serif; font-size:6.8px; fill:#6c7b77}
.sch .fl{stroke:#9aa8a4; stroke-width:1.2; fill:none}
.sch .fl-t{fill:#9aa8a4}
.sch .t-s{font-family:"IBM Plex Sans",sans-serif; font-size:6.9px; color:#4a5754;
  line-height:1.32}
.saut{break-before:page}
footer{margin-top:10mm; padding-top:2mm; border-top:1px solid #d6ded9;
  font-size:7.4pt; color:#6c7b77}
"""


def construire(d: dict) -> str:
    m = d.get("marche")
    ex = d.get("execution")
    bilan = d["bilan"]
    parts = [
        "<!doctype html><html lang=fr><head><meta charset=utf-8>",
        #  Le document est fait pour l'impression, mais il est aussi lu a l'ecran
        #  pendant qu'on le relit. Sans cette balise, un navigateur mobile pose
        #  une largeur fictive de 980 pixels et reduit tout : le compte rendu
        #  devient illisible la ou on le verifie.
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Compte rendu — la recherche d'un réglage</title>",
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&'
        'family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&'
        'display=swap">',
        f"<style>{STYLE}</style></head><body>",
        f'<p class="oeil">Banc d\'essai · {bilan["date"]}</p>',
        "<h1>Chercher un réglage <em>qui survive au fait d'être choisi</em></h1>",
        f'<p class="chapo">{bilan["chapo"]}</p>',
        tuiles(bilan["tuiles"]),
        "<h2>La chaîne de mesure</h2>",
        "<p>Cinq étages. Les deux premiers ne rejouent rien : ce qu'ils mesurent ne "
        "dépend d'aucun réglage, donc ne peut pas être surajusté. C'est de là que "
        "vient le résultat le plus solide de ce travail.</p>",
        schema_chaine(bilan["etapes"]),
        "<h2>Ce que le marché pouvait servir</h2>",
        "<p>Le moteur sert un ordre limite <b>entier, instantanément, au prix exact</b>, "
        "dès que le bas d'une bougie touche son prix. Il ne regarde jamais s'il s'est "
        "échangé quoi que ce soit. Les mises croissent géométriquement : à huit barreaux "
        "de raison 1,6, le barreau du bas porte 38 % du budget — et c'est lui qui se pose "
        "dans les creux, quand le carnet est le plus mince.</p>",
        schema_entonnoir(ex),
        bilan["dit_execution"],
        "<h2>Les dix paires, mesurées sans aucune stratégie</h2>",
        tableau_marche(m, ex),
        bilan["dit_marche"],
        '<div class="saut"></div>',
        "<h2>Ce qui a été ajouté au moteur</h2>",
        "<p>Cinq mécanismes, tous <b>inertes par défaut</b> et tous prouvés tels avant "
        "d'être utilisés. C'est la seule colonne qui compte : un champ neuf qui ne serait "
        "pas inerte rendrait faux tout ce que ce dépôt a déjà mesuré.</p>",
        schema_moteur(bilan["moteur"]),
        bilan["dit_moteur"],
        corps_etapes(d, bilan["corps_etapes"], bilan.get("dits_etapes")),
        "<h2>Ce qui est établi, et ce qui ne l'est pas</h2>",
        bilan["bilan_final"],
        f'<footer>{bilan["pied"]}</footer>',
        "</body></html>",
    ]
    return "".join(parts)


def imprimer(html: Path, pdf: Path) -> bool:
    nav = next((n for n in NAVIGATEURS if Path(n).exists()), None)
    if not nav:
        print("  aucun navigateur trouve : le PDF n'est pas genere")
        return False
    #  virtual-time-budget : sans lui, la page est imprimee avant que les
    #  polices distantes soient arrivees, et le PDF sort en Times New Roman.
    cmd = [nav, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           "--virtual-time-budget=12000", f"--print-to-pdf={pdf}",
           html.resolve().as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if pdf.exists() and pdf.stat().st_size > 5000:
        return True
    print("  impression echouee :", (r.stderr or r.stdout or "")[-400:])
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sans-pdf", action="store_true")
    ap.add_argument("--bilan", default="rapport/bilan.json")
    args = ap.parse_args()

    d = lire("docs/data/etude.json") or {}
    b = lire(args.bilan)
    if not b:
        raise SystemExit(f"{args.bilan} absent : c'est lui qui porte la prose et les "
                         f"chiffres de synthese du compte rendu")
    d["bilan"] = b
    SORTIE.mkdir(exist_ok=True)
    html = SORTIE / "compte-rendu.html"
    html.write_text(construire(d), encoding="utf-8")
    print(f"  ecrit {html.relative_to(RACINE)} ({html.stat().st_size / 1024:.0f} Ko)")
    if not args.sans_pdf:
        pdf = SORTIE / "compte-rendu.pdf"
        if imprimer(html, pdf):
            print(f"  ecrit {pdf.relative_to(RACINE)} "
                  f"({pdf.stat().st_size / 1024:.0f} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
