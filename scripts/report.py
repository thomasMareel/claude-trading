"""Rapport : Claude face au repere buy-and-hold.

    python scripts/report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import load_config  # noqa: E402
from src.engine import BENCHMARK  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.portfolio import build_positions, compute_cash  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()
BRAIN = "llm"


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
    quote = cfg.get("exchange.quote")
    init = cfg.total_capital
    fee = float(cfg.get("exchange.fee_rate", 0.001))
    slip = float(cfg.get("exchange.slippage", 0.0005))

    # ---- Claude, en valeur de liquidation comme le repere ----
    cash = compute_cash(st, BRAIN, init)
    pos = build_positions(st, BRAIN, prices)
    eq = cash + sum(p.value_quote for p in pos) * (1 - slip) * (1 - fee)
    closed = st.closed_positions(BRAIN)
    wins = sum(1 for c in closed if (c["pnl_quote"] or 0) > 0)
    curve = [float(r["total_quote"]) for r in st.equity_curve(BRAIN)] or [init]
    perf = (eq / init - 1) * 100
    api = st.api_cost_total()

    # ---- repere : valeur de liquidation aux prix courants ----
    basket = st.benchmark_basket()
    bench = None
    if basket:
        gross = sum(float(r["amount_base"]) * prices.get(r["symbol"], float(r["start_price"])) * (1 - slip)
                    for r in basket)
        bench = gross * (1 - fee)
    bcurve = [float(r["total_quote"]) for r in st.equity_curve(BENCHMARK)] or [init]

    t = Table(title="Claude face au repere", pad_edge=False)
    t.add_column("book", justify="left", no_wrap=True)
    for col in ("equity", "perf", "maxDD", "trades", "gagn.", "frais"):
        t.add_column(col, justify="right", no_wrap=True)
    t.add_column("detenu", justify="left")

    color = "green" if perf >= 0 else "red"
    t.add_row(
        "claude", f"{eq:.2f}", f"[{color}]{perf:+.2f}%[/]", f"{max_drawdown([init] + curve)*100:.1f}%",
        str(len(closed)), f"{wins}/{len(closed)}" if closed else "-", f"{st.total_fees(BRAIN):.3f}",
        ", ".join(f"{p.symbol.split('/')[0]} {p.pnl_pct:+.1f}%" for p in pos) or f"cash {cash:.2f}",
    )
    if bench is not None:
        bperf = (bench / init - 1) * 100
        bcolor = "green" if bperf >= 0 else "red"
        t.add_row(
            "repere", f"{bench:.2f}", f"[{bcolor}]{bperf:+.2f}%[/]", f"{max_drawdown([init] + bcurve)*100:.1f}%",
            "-", "-", f"{init * fee * 2:.3f}",
            ", ".join(f"{r['symbol'].split('/')[0]} {(prices[r['symbol']]/float(r['start_price'])-1)*100:+.1f}%"
                      for r in basket if r["symbol"] in prices),
        )
    console.print(t)

    start = st.first_equity_ts(BENCHMARK)
    console.print(f"[dim]capital {init:.2f} {quote}  |  depuis {start or 'aucun cycle'}  |  "
                  f"frais et slippage appliques aux deux, des deux cotes[/]")

    if bench is not None:
        gap = perf - (bench / init - 1) * 100
        gcolor = "green" if gap >= 0 else "red"
        console.print(f"\n[bold]Ecart Claude - repere : [{gcolor}]{gap:+.2f} points[/][/]")
        net = ((eq - api) / init - 1) * 100
        ncolor = "green" if net >= 0 else "red"
        console.print(f"Perf de Claude nette du cout API ({api:.2f} $ sur {st.api_calls_total()} appels) : "
                      f"[{ncolor}]{net:+.2f}%[/]")

    if closed:
        console.print("\n[bold]Trades clotures[/]")
        for c in closed[-12:]:
            pnl = float(c["pnl_quote"] or 0)
            pc = "green" if pnl >= 0 else "red"
            console.print(f"  {c['closed_at'][:16]}  {c['symbol']:<9} {float(c['entry_price']):>10.4f} -> "
                          f"{float(c['exit_price']):>10.4f}  [{pc}]{pnl:+.2f}[/]  {c['close_reason']}")

    ev = st.recent_events(8)
    if ev:
        console.print("\n[bold]Derniers evenements[/]")
        for e in reversed(ev):
            style = {"critical": "bold red", "warning": "yellow"}.get(e["level"], "dim")
            console.print(f"  [{style}]{e['ts']}  {e['source']:<14} {e['message'][:110]}[/]")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
