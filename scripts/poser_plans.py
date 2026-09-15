"""Fait lire a balayer.py les plans de scripts/plans.py, au lieu des siens.

    python scripts/poser_plans.py --verifier
    python scripts/poser_plans.py

POURQUOI UN POSEUR PLUTOT QU'UNE EDITION A LA MAIN. balayer.py est charge par
SIX PROCESSUS QUI LE RELISENT SUR LE DISQUE a chaque paire : sur Windows, un
pool cree par paire redemarre des processus qui reimportent le module. Le
modifier pendant un balayage ferait mesurer les premieres paires avec un code et
les suivantes avec un autre, sans que rien ne le signale. Ce script REFUSE donc
de poser tant qu'un balayage tourne, et il refuse aussi si la moindre ancre a
bouge — plutot que de poser a cote et de laisser un fichier a moitie converti.

Il est IDEMPOTENT : pose une seconde fois, il constate et ne fait rien.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CIBLE = RACINE / "scripts/balayer.py"

#  Ce qui disparait de balayer.py : la partie « quoi mesurer ». Chaque entree
#  est (premiere ligne, derniere ligne incluse) du bloc a retirer.
BLOCS = [
    ("# ---------------------------------------------------------------- etape 1",
     "PLANS = {1: plan_etape1}"),
]

IMPORT_AVANT = "from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402"
IMPORT_APRES = (
    "from src.grille import GrilleError, Reglages, rejouer, resume  # noqa: E402\n"
    "#  Le banc ne sait pas ce qu'il rejoue : le programme de chaque etape vit\n"
    "#  dans plans.py. C'est ce qui permet d'ecrire l'etape suivante pendant que\n"
    "#  la precedente tourne, sans toucher au fichier que les processus relisent.\n"
    "from plans import AXES_1, BESOIN_VOLUME, PLANS, TEMOIN  # noqa: E402"
)

#  ---- le volume, charge seulement quand une etape en a besoin ----
#  Le plafond de participation est le seul mecanisme qui lise le volume. Le
#  charger partout couterait un cinquieme de memoire en plus sur 253 440 bougies
#  par paire et par processus, pour un champ que personne ne regarde.
CHARGE_AVANT = """    serie = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
             for r in cx.execute(
                 "SELECT ts,open,high,low,close FROM candles WHERE symbol=? "
                 "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                 (paire, t0 - n_pre * PAS_MS, t0 + N_BLOCS * bloc_ms))]"""
CHARGE_APRES = """    colonnes = "ts,open,high,low,close" + (",volume" if cfg.get("volume") else "")
    serie = [tuple([int(r[0])] + [float(x) for x in r[1:]])
             for r in cx.execute(
                 f"SELECT {colonnes} FROM candles WHERE symbol=? "
                 "AND timeframe='5m' AND ts>=? AND ts<? ORDER BY ts",
                 (paire, t0 - n_pre * PAS_MS, t0 + N_BLOCS * bloc_ms))]"""

CFG_AVANT = '        cfg = {"frais": args.frais, "paire": paire}'
CFG_APRES = ('        cfg = {"frais": args.frais, "paire": paire,\n'
             '               "volume": args.etape in BESOIN_VOLUME}')

#  ---- le prechauffage, deduit de ce que le plan demande ----
#  Mesure faite avant de l'ecrire : sans prechauffage, un filtre de moyenne a
#  sept jours reste sans avis sur les 2 016 premieres bougies d'une tranche de
#  quarante jours, et sur ETH cela vaut 72 achats au lieu de 47 dans cette
#  fenetre, 124 au lieu de 99 sur la tranche entiere — vingt pour cent. Chaque
#  longueur de fenetre se comporterait donc differemment au DEBUT de chaque
#  bloc, precisement la ou l'etape 3 les compare.
PRE_AVANT = "    n_pre = 0                      # l'etape 1 n'utilise aucune moyenne mobile"
PRE_APRES = '''    #  Le prechauffage vaut la plus longue fenetre que le plan demande, toutes
    #  fenetres confondues : moyenne de reference, volatilite, filtre de moyenne
    #  et maximum glissant se servent tous des memes clotures precedentes.
    n_pre = max((max(int(c.get("fenetre_vol", 0)), int(c.get("filtre_moyenne", 0)),
                     int(c.get("devi_fenetre", 0)), int(c.get("moyenne_ref", 1)))
                 for c in cbs), default=0)
    if n_pre > 1:
        print(f"  prechauffage : {n_pre} bougies avant chaque tranche, "
              f"identiques pour toutes les fenetres")
    else:
        n_pre = 0'''
CHEMIN_SCRIPTS = 'sys.path.insert(0, str(RACINE))'
CHEMIN_APRES = ('sys.path.insert(0, str(RACINE))\n'
                'sys.path.insert(0, str(RACINE / "scripts"))')


def occupe() -> str | None:
    """Un balayage tourne-t-il ? On regarde les processus, pas un fichier temoin."""
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=45)
        for ligne in (r.stdout or "").splitlines():
            if "balayer.py" in ligne:
                return ligne.strip()[:120]
    except Exception as e:                      # noqa: BLE001
        return f"impossible de verifier les processus ({e})"
    return None


def poser(texte: str) -> tuple[str, list[str]]:
    faits = []
    if "from plans import" in texte:
        return texte, ["deja pose"]
    for debut, fin in BLOCS:
        if texte.count(debut) != 1 or texte.count(fin) != 1:
            raise SystemExit(f"ANCRE INTROUVABLE OU MULTIPLE, rien n'est pose :\n"
                             f"  debut {texte.count(debut)}x : {debut[:60]}\n"
                             f"  fin   {texte.count(fin)}x : {fin[:60]}")
        i = texte.index(debut)
        j = texte.index(fin) + len(fin)
        if j < i:
            raise SystemExit("ANCRES INVERSEES, rien n'est pose")
        texte = texte[:i] + texte[j:]
        faits.append(f"retire {len(range(i, j))} caracteres de plan")
    for nom, a, b in (("chemin des scripts", CHEMIN_SCRIPTS, CHEMIN_APRES),
                      ("import des plans", IMPORT_AVANT, IMPORT_APRES),
                      ("chargement du volume", CHARGE_AVANT, CHARGE_APRES),
                      ("drapeau de volume", CFG_AVANT, CFG_APRES),
                      ("prechauffage deduit du plan", PRE_AVANT, PRE_APRES)):
        if texte.count(a) != 1:
            raise SystemExit(f"ANCRE « {nom} » {texte.count(a)}x, rien n'est pose :\n"
                             f"  {a[:70]}")
        texte = texte.replace(a, b)
        faits.append(nom)
    #  random n'est plus utilise une fois le tirage parti chez plans.py.
    texte = texte.replace("import random\n", "", 1)
    return texte, faits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", action="store_true")
    ap.add_argument("--forcer", action="store_true",
                    help="poser meme si un balayage tourne (a ne jamais faire)")
    args = ap.parse_args()

    en_cours = occupe()
    if en_cours and not args.forcer:
        print(f"  REFUS : un balayage tourne encore\n    {en_cours}")
        print("  Les processus relisent balayer.py a chaque paire : le modifier "
              "maintenant\n  ferait mesurer les paires suivantes avec un autre code.")
        return 2

    src = CIBLE.read_text(encoding="utf-8")
    neuf, faits = poser(src)
    if faits == ["deja pose"]:
        print("  deja pose, rien a faire")
        return 0
    ast.parse(neuf)                       # on ne pose jamais un fichier qui ne compile pas
    for f in faits:
        print("  " + f)
    if args.verifier:
        print(f"  --verifier : {len(src) - len(neuf)} caracteres en moins, rien n'est ecrit")
        return 0
    CIBLE.write_text(neuf, encoding="utf-8")
    print(f"  ecrit {CIBLE.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
