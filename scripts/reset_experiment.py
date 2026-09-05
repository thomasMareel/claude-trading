"""Remet l'experience a zero : decisions, ordres, positions, equity, couts,
evenements, repere. Les bougies sont conservees.

    python scripts/reset_experiment.py --yes

Sans --yes, affiche seulement ce qui serait efface. A n'utiliser qu'en
paper : en reel, l'historique est la seule trace de ce qui a ete fait.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.storage import EXPERIMENT_TABLES, Storage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="efface vraiment")
    args = ap.parse_args()

    cfg = load_config()
    if cfg.mode == "live" and args.yes:
        print("Refus : engine.mode est 'live'. Remettre a zero un historique reel n'a pas de sens.")
        return 1
    st = Storage(cfg.get("storage.db_path"), cfg.get("storage.journal_path"))
    if not args.yes:
        for t in EXPERIMENT_TABLES:
            n = st._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<12} {n:>6} lignes seraient effacees")
        print("\nRelance avec --yes pour effacer. Les bougies sont conservees.")
        return 0
    counts = st.reset_experiment()
    for t, n in counts.items():
        print(f"  {t:<12} {n:>6} lignes effacees")
    print("\nExperience remise a zero. Le repere sera reconstitue au premier cycle.")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
