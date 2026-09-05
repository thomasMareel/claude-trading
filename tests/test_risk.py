"""La couche de risque est ce qui protege le capital : elle est testee
en priorite, sans reseau, sur une base SQLite en memoire."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.brains.base import BrainContext, Decision, MarketSnapshot, PositionView  # noqa: E402
from src.config import Config  # noqa: E402
from src.risk import BOOK_UNCERTAIN, RiskManager  # noqa: E402
from src.storage import Storage  # noqa: E402

CFG = Config(raw={
    "risk": {
        "max_position_pct": 0.4, "max_open_positions": 2, "min_order_value": 12.0,
        "max_round_trips_per_week": 5, "max_daily_loss_pct": 0.06,
        "kill_switch_drawdown_pct": 0.20, "stop_loss_pct": 0.08, "take_profit_pct": 0.15,
    }
})


def _ctx(cash=100.0, positions=None, rt_used=0, daily=0.0):
    snap = MarketSnapshot("BTC/USDT", 100.0, pd.DataFrame(), {})
    return BrainContext(
        cycle_id="t", brain="llm", now_iso="now", initial_capital=100.0, cash=cash,
        positions=positions or [], markets={"BTC/USDT": snap, "ETH/USDT": snap},
        recent_decisions=[], recent_trades=[], round_trips_used=rt_used,
        round_trips_budget=5, daily_pnl_pct=daily, fee_rate=0.001,
    )


def _pos(symbol="BTC/USDT", entry=100.0, px=100.0, amount=0.4):
    return PositionView(1, symbol, entry, amount, entry * amount, px, "t", entry * 0.92, entry * 1.15)


def _risk():
    return RiskManager(CFG, Storage(":memory:"))


def vet(r, ctx, d, **kw):
    kw.setdefault("min_notional", 5.0)
    kw.setdefault("cash_now", ctx.cash)
    kw.setdefault("open_now", len(ctx.positions))
    kw.setdefault("opens_this_cycle", 0)
    return r.vet(ctx, d, **kw)


def test_hold_passes():
    d, why = vet(_risk(), _ctx(), Decision("BTC/USDT", "hold"))
    assert d is not None and why is None


def test_buy_is_capped_at_max_position_pct():
    d, why = vet(_risk(), _ctx(), Decision("BTC/USDT", "buy", size_quote=90.0))
    assert why is None
    assert d.size_quote == 40.0  # 40 % de 100
    assert d.raw.get("resized_from") == 90.0


def test_buy_below_minimum_is_rejected():
    d, why = vet(_risk(), _ctx(), Decision("BTC/USDT", "buy", size_quote=8.0))
    assert d is None and "minimum" in why


def test_buy_respects_exchange_min_notional():
    d, why = vet(_risk(), _ctx(), Decision("BTC/USDT", "buy", size_quote=15.0), min_notional=20.0)
    assert d is None and "minimum 20.00" in why


def test_no_pyramiding():
    d, why = vet(_risk(), _ctx(positions=[_pos()]), Decision("BTC/USDT", "buy", size_quote=20.0))
    assert d is None and "renforcement" in why


def test_max_open_positions():
    ctx = _ctx(positions=[_pos("BTC/USDT"), _pos("ETH/USDT")])
    d, why = vet(_risk(), ctx, Decision("SOL/USDT", "buy", size_quote=15.0))
    assert d is None and "maximum 2" in why


def test_weekly_budget_counts_opens_this_cycle():
    d, why = vet(_risk(), _ctx(rt_used=4), Decision("BTC/USDT", "buy", size_quote=15.0), opens_this_cycle=1)
    assert d is None and "budget hebdo" in why


def test_daily_loss_freezes_buys_not_sells():
    ctx = _ctx(daily=-6.5, positions=[_pos()])
    d, why = vet(_risk(), ctx, Decision("ETH/USDT", "buy", size_quote=15.0))
    assert d is None and "gele" in why
    d, why = vet(_risk(), ctx, Decision("BTC/USDT", "sell"))
    assert d is not None and why is None


def test_sell_without_position_is_rejected():
    d, why = vet(_risk(), _ctx(), Decision("BTC/USDT", "sell"))
    assert d is None and "aucune position" in why


def test_buy_cannot_exceed_cash_after_earlier_buy():
    d, why = vet(_risk(), _ctx(), Decision("ETH/USDT", "buy", size_quote=20.0), cash_now=10.0)
    assert d is None and "cash 10.00" in why


def test_book_uncertain_blocks_buys_not_sells_until_acknowledged():
    r = _risk()
    r.storage.event("critical", BOOK_UNCERTAIN, "ordre au resultat inconnu")
    ctx = _ctx(positions=[_pos("ETH/USDT")])
    d, why = vet(r, ctx, Decision("BTC/USDT", "buy", size_quote=20.0))
    assert d is None and "book incertain" in why
    d, why = vet(r, ctx, Decision("ETH/USDT", "sell"))
    assert d is not None                                 # reduire l'exposition reste permis
    r.storage.acknowledge(BOOK_UNCERTAIN)
    d, why = vet(r, ctx, Decision("BTC/USDT", "buy", size_quote=20.0))
    assert d is not None and why is None


def test_forced_exits_stop_and_target():
    r = _risk()
    hit_stop = _pos(entry=100.0, px=91.0)
    hit_tp = _pos("ETH/USDT", entry=100.0, px=116.0)
    calm = _pos("SOL/USDT", entry=100.0, px=103.0)
    exits = r.forced_exits([hit_stop, hit_tp, calm])
    assert [(p.symbol, why) for p, why in exits] == [("BTC/USDT", "stop_loss"), ("ETH/USDT", "take_profit")]


def test_kill_switch_is_sticky():
    r = _risk()
    assert r.kill_switch(85.0, 100.0) is False
    assert r.kill_switch(79.0, 100.0) is True
    assert r.kill_switch(100.0, 100.0) is True  # reste declenche meme si l'equity remonte


def test_stop_and_target_levels():
    stop, tp = _risk().stop_and_target(200.0)
    assert stop == 184.0 and tp == 230.0
