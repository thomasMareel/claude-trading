"""Cycle complet en paper sur des donnees synthetiques, sans reseau.
Verifie que l'argent est conserve : cash + positions = capital +/- PnL - frais."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src.brains.base import BrainContext, Decision  # noqa: E402
from src.config import Config  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.executor import PaperExecutor  # noqa: E402
from src.portfolio import compute_cash  # noqa: E402
from src.risk import RiskManager  # noqa: E402
from src.storage import Storage  # noqa: E402

CFG = Config(raw={
    "experiment": {"total_capital": 100.0, "allocation": {"llm": 0.5, "rules": 0.5}},
    "exchange": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "4h",
                 "lookback_candles": 260, "fee_rate": 0.001, "slippage": 0.0005},
    "risk": {"max_position_pct": 0.4, "max_open_positions": 2, "min_order_value": 12.0,
             "max_round_trips_per_week": 3, "max_daily_loss_pct": 0.06,
             "kill_switch_drawdown_pct": 0.25, "stop_loss_pct": 0.08, "take_profit_pct": 0.15},
    "rules": {"ema_fast": 50, "ema_slow": 200, "rsi_period": 14, "atr_period": 14},
    "engine": {"mode": "paper"},
})


class FakeData:
    """Exchange factice : prix pilotable, bougies synthetiques."""

    def __init__(self, prices: dict[str, float]):
        self.prices = dict(prices)

    def fetch_prices(self, symbols):
        return {s: self.prices[s] for s in symbols}

    def fetch_ohlcv(self, symbol, timeframe, limit=300, since=None):
        px = self.prices[symbol]
        rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
        closes = px * np.cumprod(1 + rng.normal(0, 0.005, limit))
        closes = closes * (px / closes[-1])
        t0 = 1_700_000_000_000
        return [[t0 + i * 14_400_000, c, c * 1.01, c * 0.99, c, 1000.0] for i, c in enumerate(closes)]

    def min_notional(self, symbol):
        return 5.0

    def amount_to_precision(self, symbol, amount):
        return float(f"{amount:.6f}")


class ScriptedBrain:
    """Cerveau qui joue un scenario : liste de listes de Decision, un par cycle."""

    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.seen: list[BrainContext] = []

    def decide(self, ctx):
        self.seen.append(ctx)
        return self.script.pop(0) if self.script else [Decision(s, "hold") for s in ctx.markets]


def make_engine(prices, brains):
    st = Storage(":memory:")
    data = FakeData(prices)
    ex = PaperExecutor(0.001, 0.0005, data.amount_to_precision)
    return Engine(CFG, st, data, ex, brains, RiskManager(CFG, st)), st, data


def test_buy_then_sell_conserves_money():
    brain = ScriptedBrain("rules", [
        [Decision("BTC/USDT", "buy", size_quote=20.0, reasoning="test")],
        [Decision("BTC/USDT", "sell", reasoning="test")],
    ])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, {"rules": brain})

    eng.run_cycle()
    pos = st.open_positions("rules")
    assert len(pos) == 1
    cash = compute_cash(st, "rules", 50.0)
    assert 29.9 < cash < 30.1  # 50 - 20 - frais
    assert abs(pos[0]["entry_price"] - 100.05) < 1e-6  # slippage 0.05%

    data.prices["BTC/USDT"] = 105.0
    eng.run_cycle()
    assert st.open_positions("rules") == []
    closed = st.closed_positions("rules")
    assert len(closed) == 1 and closed[0]["close_reason"] == "signal"
    pnl = float(closed[0]["pnl_quote"])
    # +5% brut sur ~20, moins ~0.2% de frais et 0.1% de slippage
    assert 0.9 < pnl < 1.0, pnl
    final_cash = compute_cash(st, "rules", 50.0)
    assert abs(final_cash - (50.0 + pnl)) < 1e-6


def test_stop_loss_forces_exit_before_brain_speaks():
    brain = ScriptedBrain("rules", [[Decision("BTC/USDT", "buy", size_quote=20.0)]])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, {"rules": brain})
    eng.run_cycle()
    data.prices["BTC/USDT"] = 90.0  # -10% : sous le stop a -8%
    eng.run_cycle()
    closed = st.closed_positions("rules")
    assert len(closed) == 1 and closed[0]["close_reason"] == "stop_loss"
    # le cerveau a bien ete appele au 2e cycle avec 0 position
    assert brain.seen[-1].positions == []


def test_second_buy_same_cycle_uses_remaining_cash():
    brain = ScriptedBrain("rules", [[
        Decision("BTC/USDT", "buy", size_quote=20.0),
        Decision("ETH/USDT", "buy", size_quote=20.0),
    ]])
    eng, st, _ = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, {"rules": brain})
    eng.run_cycle()
    assert len(st.open_positions("rules")) == 2
    assert compute_cash(st, "rules", 50.0) > 9.0


def test_third_buy_rejected_by_max_open_positions():
    brain = ScriptedBrain("rules", [[
        Decision("BTC/USDT", "buy", size_quote=15.0),
        Decision("ETH/USDT", "buy", size_quote=15.0),
        Decision("SOL/USDT", "buy", size_quote=15.0),
    ]])
    cfg3 = Config(raw={**CFG.raw, "exchange": {**CFG.raw["exchange"], "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"]}})
    st = Storage(":memory:")
    data = FakeData({"BTC/USDT": 100.0, "ETH/USDT": 10.0, "SOL/USDT": 5.0})
    eng = Engine(cfg3, st, data, PaperExecutor(0.001, 0.0005, data.amount_to_precision),
                 {"rules": brain}, RiskManager(cfg3, st))
    eng.run_cycle()
    refused = [d for d in st.decisions_for_cycle(st.recent_decisions("rules", 1)[0]["cycle_id"]) if not d["accepted"]]
    assert len(st.open_positions("rules")) == 2
    assert any("maximum 2" in (d["reject_reason"] or "") for d in refused)


def test_kill_switch_liquidates_everything():
    brain = ScriptedBrain("rules", [[Decision("BTC/USDT", "buy", size_quote=20.0)]])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, {"rules": brain})
    eng.run_cycle()
    data.prices["BTC/USDT"] = 20.0  # book total passe sous -25%
    eng.run_cycle()
    assert st.kill_switch_tripped()
    assert st.open_positions() == []
    assert st.closed_positions("rules")[0]["close_reason"] == "kill_switch"
    # cycle suivant : plus rien ne s'ouvre meme avec un signal
    brain.script.append([Decision("ETH/USDT", "buy", size_quote=20.0)])
    eng.run_cycle()
    assert st.open_positions() == []


def test_crashing_brain_does_not_break_cycle():
    class Boom:
        name = "rules"
        def decide(self, ctx):
            raise RuntimeError("kaboom")
    eng, st, _ = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, {"rules": Boom()})
    eng.run_cycle()
    ev = st.recent_events(5)
    assert any("kaboom" in e["message"] for e in ev)
    assert st.last_equity("rules") is not None
