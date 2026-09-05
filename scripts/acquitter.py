"""Acquitte un incident "book incertain" apres verification MANUELLE du
compte Binance. Tant que ce n'est pas fait, la couche de risque refuse
tout achat.

    python scripts/acquitter.py

A ne lancer qu'apres avoir compare, a la main, les positions ouvertes en
base (python scripts/report.py) et les soldes reels du compte spot.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.risk import BOOK_UNCERTAIN  # noqa: E402
from src.storage import Storage  # noqa: E402


def main() -> int:
    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    if not st.is_flagged(BOOK_UNCERTAIN):
        print("Rien a acquitter : aucun incident 'book incertain' en attente.")
        return 0
    for e in reversed(st.recent_events(30)):
        if e["source"] == BOOK_UNCERTAIN and e["level"] == "critical":
            print(f"incident : {e['ts']}  {e['message']}")
    st.acknowledge(BOOK_UNCERTAIN)
    print("\nAcquitte. Les achats sont de nouveau autorises au prochain cycle.")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
