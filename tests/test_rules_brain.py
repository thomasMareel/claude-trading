from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src.brains.base import BrainContext, MarketSnapshot, PositionView  # noqa: E402
from src.brains.rules_brain import RulesBrain  # noqa: E402
from src.config import Config  # noqa: E402
from src.indicators import candles_to_df, enrich  # noqa: E402

CFG = Config(raw={"rules": {"ema_fast": 50, "ema_slow": 200, "rsi_period": 14,
                            "atr_period": 14, "rsi_overbought": 72},
                  "risk": {"max_position_pct": 0.4}})


def _df(closes):
    t0 = 1_700_000_000_000
    rows = [[t0 + i * 14_400_000, c, c * 1.005, c * 0.995, c, 1000.0] for i, c in enumerate(closes)]
    return enrich(candles_to_df(rows), ema_fast=50, ema_slow=200, rsi_period=14, atr_period=14)


def _ctx(dfs, positions=None):
    markets = {s: MarketSnapshot(s, float(df["close"].iloc[-1]), df, {}) for s, df in dfs.items()}
    return BrainContext("c", "rules", "now", 50.0, 50.0, positions or [], markets,
                        [], [], 0, 3, 0.0, 0.001)


def uptrend_with_pullback_bounce(n=300, seed=1):
    """Tendance haussiere reguliere, un creux sous l'EMA50 puis rebond a la fin."""
    rng = np.random.default_rng(seed)
    base = 100 * np.cumprod(1 + rng.normal(0.002, 0.004, n))
    # sur une tendance a +0,2 %/bougie, l'EMA50 traine ~5 % sous le prix :
    # il faut un creux plus profond pour passer dessous, puis un rebond net.
    base[-6:-2] *= 0.92
    base[-2:] = base[-7] * 1.01
    return base


def test_buys_on_fresh_cross_in_uptrend():
    df = _df(uptrend_with_pullback_bounce())
    last, prev = df.iloc[-1], df.iloc[-2]
    assert last["ema_fast"] > last["ema_slow"]
    assert last["close"] > last["ema_fast"]
    decs = RulesBrain(CFG).decide(_ctx({"BTC/USDT": df}))
    actions = {d.symbol: d.action for d in decs}
    assert actions["BTC/USDT"] == "buy", [d.reasoning for d in decs]
    buy = next(d for d in decs if d.action == "buy")
    assert buy.size_quote == 20.0


def test_holds_in_downtrend():
    rng = np.random.default_rng(2)
    closes = 100 * np.cumprod(1 + rng.normal(-0.003, 0.004, 300))
    decs = RulesBrain(CFG).decide(_ctx({"BTC/USDT": _df(closes)}))
    assert all(d.action == "hold" for d in decs)
    assert "baissier" in decs[0].reasoning


def test_sells_open_position_when_regime_flips():
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + rng.normal(-0.003, 0.004, 300))
    df = _df(closes)
    pos = PositionView(1, "BTC/USDT", 120.0, 0.1, 12.0, float(df["close"].iloc[-1]), "t", None, None)
    decs = RulesBrain(CFG).decide(_ctx({"BTC/USDT": df}, [pos]))
    assert decs[0].action == "sell" and "baissier" in decs[0].reasoning


def test_picks_single_best_candidate():
    a = _df(uptrend_with_pullback_bounce(seed=1))
    b = _df(uptrend_with_pullback_bounce(seed=4) * 0.5)
    decs = RulesBrain(CFG).decide(_ctx({"BTC/USDT": a, "ETH/USDT": b}))
    buys = [d for d in decs if d.action == "buy"]
    assert len(buys) <= 1
