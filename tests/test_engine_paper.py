"""Cycle complet en paper sur des donnees synthetiques, sans reseau.
Verifie que l'argent est conserve, que le repere est equitable, que les
garde-fous tiennent et que le chemin des ordres ne peut pas perdre une
position."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from src.brains.base import BrainContext, Decision  # noqa: E402
from src.config import Config  # noqa: E402
from src.engine import BENCHMARK, Engine  # noqa: E402
from src.exchange import OrderUncertainError  # noqa: E402
from src.executor import PaperExecutor  # noqa: E402
from src.portfolio import compute_cash  # noqa: E402
from src.risk import BOOK_UNCERTAIN, RiskManager  # noqa: E402
from src.storage import Storage  # noqa: E402

CFG = Config(raw={
    "experiment": {"total_capital": 100.0},
    "exchange": {"quote": "USDT", "symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "4h",
                 "lookback_candles": 260, "fee_rate": 0.001, "slippage": 0.0005},
    "risk": {"max_position_pct": 0.4, "max_open_positions": 2, "min_order_value": 12.0,
             "max_round_trips_per_week": 5, "max_daily_loss_pct": 0.06,
             "kill_switch_drawdown_pct": 0.20, "stop_loss_pct": 0.08, "take_profit_pct": 0.15},
    "indicators": {"ema_fast": 50, "ema_slow": 200, "rsi_period": 14, "atr_period": 14},
    "engine": {"mode": "paper"},
    "alerts": {"enabled": False},
})


class FakeData:
    """Exchange factice : prix pilotable, bougies synthetiques."""

    def __init__(self, prices: dict[str, float]):
        self.prices = dict(prices)

    def fetch_prices(self, symbols, *, strict=True):
        out = {s: self.prices[s] for s in symbols if s in self.prices}
        if strict and len(out) < len(symbols):
            raise RuntimeError(f"prix manquant : {[s for s in symbols if s not in out]}")
        return out

    def fetch_ohlcv(self, symbol, timeframe, limit=300, since=None):
        px = self.prices.get(symbol, 100.0)
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
    """Cerveau qui joue un scenario : liste de listes de Decision, une par cycle."""

    name = "llm"

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[BrainContext] = []

    def decide(self, ctx):
        self.seen.append(ctx)
        return self.script.pop(0) if self.script else [Decision(s, "hold") for s in ctx.markets]


class CrashingBrain:
    name = "llm"

    def decide(self, ctx):
        raise RuntimeError("boum")


class UncertainExecutor(PaperExecutor):
    """Simule un delai reseau apres l'envoi d'un ordre reel."""

    def buy(self, *a, **k):
        raise OrderUncertainError("ordre envoye, resultat inconnu apres 3 relectures")


def make_engine(prices, brain, cfg=CFG, executor=None):
    st = Storage(":memory:")
    data = FakeData(prices)
    ex = executor or PaperExecutor(0.001, 0.0005, data.amount_to_precision)
    return Engine(cfg, st, data, ex, brain, RiskManager(cfg, st)), st, data


BUY_BTC = Decision("BTC/USDT", "buy", size_quote=40.0, reasoning="test")


# ---------------------------------------------------------------- argent
def test_buy_then_sell_conserves_money():
    brain = ScriptedBrain([[BUY_BTC], [Decision("BTC/USDT", "sell", reasoning="test")]])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, brain)

    eng.run_cycle()
    pos = st.open_positions("llm")
    assert len(pos) == 1
    cash = compute_cash(st, "llm", 100.0)
    assert 59.9 < cash < 60.0                          # 100 - 40 - frais
    assert abs(pos[0]["entry_price"] - 100.05) < 1e-6  # slippage 0.05 %

    data.prices["BTC/USDT"] = 105.0
    eng.run_cycle()
    assert st.open_positions("llm") == []
    closed = st.closed_positions("llm")
    assert len(closed) == 1 and closed[0]["close_reason"] == "signal"
    pnl = float(closed[0]["pnl_quote"])
    assert 1.8 < pnl < 1.95, pnl                        # +5 % brut moins frais et slippage des deux cotes
    assert abs(compute_cash(st, "llm", 100.0) - (100.0 + pnl)) < 1e-6


def test_buy_fill_is_atomic_order_and_position_or_nothing():
    st = Storage(":memory:")
    with pytest.raises(Exception):
        st.record_buy_fill(
            cycle_id="c", brain="llm", symbol="BTC/USDT", mode="paper", price=100.0,
            amount_base=0.4, value_quote=40.0, fee_quote=0.04, exchange_id=None, decision_id=None,
            stop_loss=object(), take_profit=115.0,       # non stockable : la 2e insertion echoue
        )
    assert st.orders_for("llm") == []                   # la 1re a ete annulee avec elle
    assert st.open_positions("llm") == []


# ---------------------------------------------------------------- garde-fous
def test_stop_loss_forces_exit_before_brain_speaks():
    brain = ScriptedBrain([[BUY_BTC]])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, brain)
    eng.run_cycle()
    data.prices["BTC/USDT"] = 90.0                      # sous le stop a -8 %
    eng.run_cycle()
    closed = st.closed_positions("llm")
    assert len(closed) == 1 and closed[0]["close_reason"] == "stop_loss"
    assert brain.seen[-1].positions == []               # le cerveau a vu un book vide


def test_second_buy_same_cycle_uses_remaining_cash():
    brain = ScriptedBrain([[BUY_BTC, Decision("ETH/USDT", "buy", size_quote=40.0)]])
    eng, st, _ = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, brain)
    eng.run_cycle()
    assert len(st.open_positions("llm")) == 2
    assert 19.0 < compute_cash(st, "llm", 100.0) < 20.0


def test_third_buy_rejected_by_max_open_positions():
    brain = ScriptedBrain([[
        Decision("BTC/USDT", "buy", size_quote=30.0),
        Decision("ETH/USDT", "buy", size_quote=30.0),
        Decision("SOL/USDT", "buy", size_quote=30.0),
    ]])
    cfg3 = Config(raw={**CFG.raw, "exchange": {**CFG.raw["exchange"], "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"]}})
    eng, st, _ = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0, "SOL/USDT": 5.0}, brain, cfg=cfg3)
    eng.run_cycle()
    cycle = st.recent_decisions("llm", 1)[0]["cycle_id"]
    refused = [d for d in st.decisions_for_cycle(cycle) if not d["accepted"]]
    assert len(st.open_positions("llm")) == 2
    assert any("maximum 2" in (d["reject_reason"] or "") for d in refused)


def test_kill_switch_liquidates_everything_and_is_final():
    brain = ScriptedBrain([[BUY_BTC]])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, brain)
    eng.run_cycle()
    data.prices["BTC/USDT"] = 20.0                      # equity ~68 : -32 %, sous le seuil de 20 %
    eng.run_cycle()
    assert st.kill_switch_tripped()
    assert st.open_positions() == []
    assert st.closed_positions("llm")[0]["close_reason"] == "kill_switch"
    brain.script.append([Decision("ETH/USDT", "buy", size_quote=40.0)])
    eng.run_cycle()
    assert st.open_positions() == []                    # plus rien ne s'ouvre jamais


# ---------------------------------------------------------------- chien de garde
def test_watchdog_closes_stop_between_cycles():
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, ScriptedBrain([[BUY_BTC]]))
    eng.run_cycle()
    assert eng.check_stops() == 0
    data.prices["BTC/USDT"] = 91.0
    assert eng.check_stops() == 1
    closed = st.closed_positions("llm")
    assert len(closed) == 1 and closed[0]["close_reason"] == "stop_loss"
    assert st.recent_decisions("llm", 1)[0]["cycle_id"].startswith("WD")
    assert st.last_equity("llm")["positions_value"] == 0.0


def test_watchdog_trips_kill_switch():
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, ScriptedBrain([[BUY_BTC]]))
    eng.run_cycle()
    data.prices["BTC/USDT"] = 20.0
    assert eng.check_stops() == 1
    assert st.kill_switch_tripped() and st.open_positions() == []


def test_watchdog_tolerates_one_missing_price():
    brain = ScriptedBrain([[BUY_BTC, Decision("ETH/USDT", "buy", size_quote=40.0)]])
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, brain)
    eng.run_cycle()
    assert len(st.open_positions("llm")) == 2
    del data.prices["ETH/USDT"]                          # ETH ne repond plus
    data.prices["BTC/USDT"] = 91.0                       # mais BTC touche son stop
    assert eng.check_stops() == 1                        # BTC est quand meme sorti
    still = st.open_positions("llm")
    assert len(still) == 1 and still[0]["symbol"] == "ETH/USDT"


# ---------------------------------------------------------------- robustesse
def test_in_progress_candle_is_excluded_from_indicators():
    class LiveData(FakeData):
        def fetch_ohlcv(self, symbol, timeframe, limit=300, since=None):
            rows = super().fetch_ohlcv(symbol, timeframe, limit - 1, since)
            rows.append([int(time.time() * 1000) - 60_000, 1e6, 1e6, 1e6, 1e6, 1.0])
            return rows

    st = Storage(":memory:")
    data = LiveData({"BTC/USDT": 100.0, "ETH/USDT": 10.0})
    eng = Engine(CFG, st, data, PaperExecutor(0.001, 0.0005, data.amount_to_precision),
                 ScriptedBrain([]), RiskManager(CFG, st))
    markets = eng._refresh_markets()
    assert markets["BTC/USDT"].df["close"].iloc[-1] < 1e5   # la bougie ouverte est ignoree
    assert markets["BTC/USDT"].price == 100.0               # mais le prix live est la
    assert st.candle_count("BTC/USDT", "4h") == 261          # 260 cloturees + 1 en cours, toutes stockees


def test_crashing_brain_does_not_break_cycle():
    eng, st, _ = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, CrashingBrain())
    cycle = eng.run_cycle()
    decs = st.decisions_for_cycle(cycle)
    assert len(decs) == 2 and all(d["action"] == "hold" for d in decs)
    assert any("boum" in (d["reasoning"] or "") for d in decs)
    assert any(e["level"] == "warning" and "brain_llm" == e["source"] for e in st.recent_events())


# ---------------------------------------------------------------- repere
def test_benchmark_is_built_on_first_cycle_with_fees_both_sides_and_tracks_prices():
    eng, st, data = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, ScriptedBrain([]))
    eng.run_cycle()
    basket = st.benchmark_basket()
    assert [r["symbol"] for r in basket] == ["BTC/USDT", "ETH/USDT"]
    assert all(abs(float(r["cost_quote"]) - 50.0) < 1e-9 for r in basket)
    v1 = float(st.last_equity(BENCHMARK)["total_quote"])
    # 100 x (1-frais)/(1+slip) a l'entree, x (1-slip)(1-frais) a la sortie ~ 99.70
    assert 99.5 < v1 < 99.9, v1
    assert float(st.last_equity("llm")["total_quote"]) == 100.0   # Claude n'a rien fait

    data.prices["BTC/USDT"] = 200.0                     # la moitie du panier double
    eng.run_cycle()
    v2 = float(st.last_equity(BENCHMARK)["total_quote"])
    assert 148.0 < v2 < 151.0, v2
    assert len(st.benchmark_basket()) == 2              # le panier n'est jamais reconstitue


def test_benchmark_waits_for_the_first_real_answer_of_the_trader():
    class ApiBrain(ScriptedBrain):
        requires_api = True

    eng, st, _ = make_engine({"BTC/USDT": 100.0, "ETH/USDT": 10.0}, ApiBrain([]))
    eng.run_cycle()
    assert st.benchmark_basket() == []                  # pas de t0 tant que Claude n'a pas repondu
    assert st.last_equity(BENCHMARK) is None
    st.record_api_cost("claude-opus-5", 1, 1, 0.01)     # premier appel reussi
    eng.run_cycle()
    assert len(st.benchmark_basket()) == 2
    assert any(e["source"] == "protocol_start" for e in st.recent_events())


# ---------------------------------------------------------------- ordres reels incertains
def test_uncertain_order_freezes_buys_until_acknowledged():
    brain = ScriptedBrain([[BUY_BTC], [BUY_BTC], [BUY_BTC]])
    data = FakeData({"BTC/USDT": 100.0, "ETH/USDT": 10.0})
    st = Storage(":memory:")
    eng = Engine(CFG, st, data, UncertainExecutor(0.001, 0.0005, data.amount_to_precision),
                 brain, RiskManager(CFG, st))
    eng.run_cycle()
    assert st.open_positions("llm") == []               # rien n'a ete ecrit sur une incertitude
    assert st.is_flagged(BOOK_UNCERTAIN)
    assert any(e["level"] == "critical" and e["source"] == BOOK_UNCERTAIN for e in st.recent_events())

    eng.executor = PaperExecutor(0.001, 0.0005, data.amount_to_precision)   # le reseau est revenu
    cycle2 = eng.run_cycle()
    d = [x for x in st.decisions_for_cycle(cycle2) if x["symbol"] == "BTC/USDT"][-1]   # meme seconde = meme cycle_id : la derniere
    assert not d["accepted"] and "book incertain" in d["reject_reason"]
    assert st.open_positions("llm") == []               # toujours gele

    st.acknowledge(BOOK_UNCERTAIN)                      # l'humain a verifie le compte
    eng.run_cycle()
    assert len(st.open_positions("llm")) == 1           # les achats reprennent
