"""Execute UN cycle de decision et s'arrete. Ideal pour un planificateur
externe (Task Scheduler Windows, cron) ou pour tester a la main.

    python scripts/run_cycle.py            # mode de config.yaml
    python scripts/run_cycle.py --paper    # force le paper
    python scripts/run_cycle.py --live     # force le live (exige LIVE_ARMED)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.engine import build_engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true")
    g.add_argument("--live", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    mode = "paper" if args.paper else "live" if args.live else None
    engine = build_engine(cfg, mode_override=mode)
    try:
        engine.run_cycle()
    finally:
        engine.storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
