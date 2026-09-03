"""Charge l'historique de bougies pour le backtest et pour amorcer les
indicateurs (l'EMA 200 sur du 4h a besoin de ~35 jours).

    python scripts/fetch_history.py --days 365
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402

from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    x = Exchange(cfg, trading=False)
    since = int((time.time() - args.days * 86400) * 1000)
    for s in cfg.symbols:
        rows = x.fetch_ohlcv_full(s, cfg.timeframe, since)
        n = st.upsert_candles(s, cfg.timeframe, rows)
        console.print(f"{s:<10} {n:>6} bougies {cfg.timeframe}  "
                      f"(total en base : {st.candle_count(s, cfg.timeframe)})")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
