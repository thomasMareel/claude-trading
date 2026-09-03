"""Rapport de performance : les deux cerveaux face au temoin buy-and-hold.

    python scripts/report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.portfolio import build_positions, compute_cash  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()


def max_drawdown(curve: list[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def main() -> int:
    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    x = Exchange(cfg, trading=False)
    prices = x.fetch_prices(cfg.symbols)

    t = Table(title="Performance par cerveau", pad_edge=False)
    t.add_column("cerveau", justify="left", no_wrap=True)
    for col in ("equity", "perf", "maxDD", "trades", "gagn.", "frais"):
        t.add_column(col, justify="right", no_wrap=True)
    t.add_column("positions ouvertes", justify="left")

    open_pos: dict[str, list] = {}
    for name in ("llm", "rules"):
        init = cfg.brain_capital(name)
        if init <= 0:
            continue
        cash = compute_cash(st, name, init)
        pos = build_positions(st, name, prices)
        open_pos[name] = pos
        eq = cash + sum(p.value_quote for p in pos)
        closed = st.closed_positions(name)
        wins = sum(1 for c in closed if (c["pnl_quote"] or 0) > 0)
        curve = [float(r["total_quote"]) for r in st.equity_curve(name)] or [init]
        mdd = max_drawdown([init] + curve)
        perf = (eq / init - 1) * 100
        color = "green" if perf >= 0 else "red"
        t.add_row(
            name, f"{eq:.2f}", f"[{color}]{perf:+.2f}%[/]", f"{mdd*100:.1f}%",
            str(len(closed)), f"{wins}/{len(closed)}" if closed else "-",
            f"{st.total_fees(name):.3f}",
            ", ".join(f"{p.symbol.split('/')[0]} {p.pnl_pct:+.1f}%" for p in pos) or "-",
        )
    console.print(t)
    console.print(f"[dim]capital initial par cerveau : {cfg.brain_capital('llm'):.2f} "
                  f"{cfg.get('exchange.quote')}  |  maxDD calcule sur les releves d'equity[/]")

    # ---- temoin buy-and-hold : chaque symbole detenu depuis le premier cycle ----
    first_ts = min(
        [r["ts"] for r in st.equity_curve("llm")[:1]] + [r["ts"] for r in st.equity_curve("rules")[:1]],
        default=None,
    )
    if first_ts:
        from datetime import datetime
        start_ms = int(datetime.fromisoformat(first_ts).timestamp() * 1000)
        console.print(f"\n[bold]Temoins buy-and-hold[/] depuis le premier cycle ({first_ts}), "
                      f"frais d'entree 0.1 % inclus :")
        for sym in cfg.symbols:
            row = st._conn.execute(
                "SELECT close FROM candles WHERE symbol=? AND timeframe=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                (sym, cfg.timeframe, start_ms),
            ).fetchone()
            if row:
                start_px = float(row["close"])
                bh = (prices[sym] / start_px - 1) * 100 - 0.1
                color = "green" if bh >= 0 else "red"
                console.print(f"  {sym:<10} [{color}]{bh:+.2f}%[/]  (de {start_px:.2f} a {prices[sym]:.2f})")

    console.print(f"\n[dim]cout API total : {st.api_cost_total():.3f} $[/]")
    ev = st.recent_events(8)
    if ev:
        console.print("\n[bold]Derniers evenements[/]")
        for e in reversed(ev):
            style = {"critical": "bold red", "warning": "yellow"}.get(e["level"], "dim")
            console.print(f"  [{style}]{e['ts']}  {e['source']:<12} {e['message'][:120]}[/]")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
