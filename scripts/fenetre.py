"""Les fenetres de l'experience : une fenetre = un mandat, du t0 a la cloture.

    python scripts/fenetre.py                              # ou en est la fenetre, mandats disponibles
    python scripts/fenetre.py --clore --mandat tendance    # archive, change de mandat, remet a zero
    python scripts/fenetre.py --clore --yes                # idem, sans confirmation, meme mandat

Clore une fenetre :
  1. calcule son bilan avec src/metrics (le meme calcul que le tableau de bord)
     et l'ajoute a docs/data/fenetres.json, l'archive publique ;
  2. ecrit le mandat choisi dans config.yaml (experiment.mandate) ;
  3. remet l'experience a zero (bougies conservees). Le prochain cycle ou
     Claude repond devient le t0 de la nouvelle fenetre.
Puis il faut redemarrer le bot pour qu'il lise la nouvelle configuration.

Refuse en mode live : on ne remet pas a zero un historique reel.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import mandates, metrics  # noqa: E402
from src.config import ROOT, load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.storage import Storage  # noqa: E402

ARCHIVE = ROOT / "docs" / "data" / "fenetres.json"
CONFIG = ROOT / "config.yaml"


def set_mandate_in_config(path: Path, mandate_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r'^(\s*)mandate:\s*.*$', re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda m: f'{m.group(1)}mandate: "{mandate_id}"', text, count=1)
    else:
        text = re.sub(r'^(experiment:\s*\n)', lambda m: m.group(1) + f'  mandate: "{mandate_id}"\n', text, count=1, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")


def window_summary(st: Storage, cfg, prices: dict[str, float]) -> dict | None:
    m = metrics.compute_all(st, cfg, prices)
    win = m["window"]
    if win is None:
        return None
    b, bench, proc, bias = m["bilan"], m["benchmarks"], m["process"], m["bias"]["protocol"]
    return {
        "cloturee_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mandat": win["mandate"],
        "phase": win["phase"],
        "t0": win["t0"],
        "jours": win["days"],
        "regime": win["regime"],
        "commit_t0": win["git_commit"],
        "derive_config": win["config_drift"],
        "bilan": {"trading_brut": b["trading_brut"], "perf_pct": b["perf_pct"], "cout_api": b["api_cost"],
                  "net": b["net"], "appels": b["api_calls"], "frais": b["fees_total"]},
        "reperes": {"b1_pct": bench["b1_pct"], "b2_pct": bench["b2_pct"], "ecart_b2": bench["excess_b2"],
                    "exposition_moyenne": bench["mean_exposure"], "drawdown_max_pct": bench["max_drawdown_pct"]},
        "processus": {"trades_clos": proc["trades_closed"], "gagnants": proc["wins"], "refus_pct": proc["refusal_rate"],
                      "gels": proc["daily_freezes"], "cycles": proc["cycles"], "usage_budget": proc["budget_use"]},
        "bias": {"justesse": bias["accuracy"], "n": bias["scored"]},
        "verdict": "non prononce ici : voir docs/protocole.md section 6 et scripts/singe.py",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clore", action="store_true", help="archive la fenetre courante et en ouvre une nouvelle")
    ap.add_argument("--mandat", help="id du mandat de la nouvelle fenetre (defaut : le mandat courant)")
    ap.add_argument("--yes", action="store_true", help="ne demande pas confirmation")
    args = ap.parse_args()

    cfg = load_config()
    all_m = mandates.load_all()
    st = Storage(cfg.get("storage.db_path"), cfg.get("storage.journal_path"))
    t0 = st.first_equity_ts(metrics.BENCHMARK)

    print(f"mandat courant : {cfg.mandate} ({all_m[cfg.mandate].nom})")
    print(f"fenetre        : {'ouverte depuis ' + t0 if t0 else 'pas encore ouverte (aucun t0)'}")
    print("mandats disponibles :")
    for mid, m in all_m.items():
        mark = "*" if mid == cfg.mandate else " "
        print(f"  {mark} {mid:<14} {m.nom:<32} {m.famille}")

    if not args.clore:
        return 0
    if cfg.mode == "live":
        print("\nRefus : engine.mode est 'live'. On ne remet pas a zero un historique reel.")
        return 1
    new_id = args.mandat or cfg.mandate
    if new_id not in all_m:
        print(f"\nmandat inconnu : {new_id!r}")
        return 1
    if not args.yes:
        print(f"\nCela va archiver la fenetre courante, passer au mandat {new_id!r} et remettre "
              f"l'experience a zero. Relance avec --yes pour confirmer.")
        return 0

    if t0:
        prices = Exchange(cfg, trading=False).fetch_prices(cfg.symbols, strict=False)
        summary = window_summary(st, cfg, prices)
        archive = json.loads(ARCHIVE.read_text(encoding="utf-8")) if ARCHIVE.exists() else []
        archive.append(summary)
        ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVE.write_text(json.dumps(archive, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nfenetre archivee dans {ARCHIVE.relative_to(ROOT)} ({len(archive)} fenetre(s))")
    else:
        print("\naucune fenetre a archiver (pas de t0)")

    set_mandate_in_config(CONFIG, new_id)
    counts = st.reset_experiment()
    st.close()
    print(f"config.yaml : experiment.mandate = {new_id!r}")
    print("experience remise a zero : " + ", ".join(f"{t} {n}" for t, n in counts.items() if n))
    print("\nRedemarre le bot (fermer la fenetre noire, relancer start_paper_detached.bat). "
          "Le prochain cycle ou Claude repond ouvre la nouvelle fenetre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
