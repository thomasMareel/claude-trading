"""Comptabilite des remplissages d'ordres reels, sans reseau.

Binance preleve les frais sur l'actif recu. Si le book ne le reflete pas,
la vente suivante echoue pour solde insuffisant. Invariant achat :
value_quote + fee_quote == quote reellement sorti du compte."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src.config import Config  # noqa: E402
from src.exchange import Exchange, ExchangeError  # noqa: E402

CFG = Config(raw={"exchange": {"id": "binance", "quote": "USDT", "fee_rate": 0.001}})


def make_x():
    return Exchange(CFG, trading=False)  # aucun appel reseau a la construction


def test_buy_fee_in_base_reduces_holdings():
    order = {"id": "1", "filled": 0.0002, "average": 100_000.0, "cost": 20.0,
             "fees": [{"currency": "BTC", "cost": 0.0000002}]}
    f = make_x()._normalize_fill(order, "BTC/USDT", "buy")
    assert f["amount_base"] == pytest.approx(0.0001998)
    assert f["fee_quote"] == pytest.approx(0.02)
    assert f["value_quote"] + f["fee_quote"] == pytest.approx(20.0)


def test_sell_fee_in_quote():
    order = {"id": "2", "filled": 0.0002, "average": 105_000.0, "cost": 21.0,
             "fee": {"currency": "USDT", "cost": 0.021}}
    f = make_x()._normalize_fill(order, "BTC/USDT", "sell")
    assert f["amount_base"] == 0.0002
    assert f["value_quote"] == 21.0
    assert f["fee_quote"] == pytest.approx(0.021)


def test_bnb_fee_is_estimated_conservatively():
    order = {"id": "3", "filled": 0.0002, "average": 100_000.0, "cost": 20.0,
             "fees": [{"currency": "BNB", "cost": 0.00003}]}
    f = make_x()._normalize_fill(order, "BTC/USDT", "buy")
    assert f["amount_base"] == 0.0002          # rien preleve sur le BTC
    assert f["value_quote"] == 20.0
    assert f["fee_quote"] == pytest.approx(0.02)  # 0.1 % estime, paye en BNB hors book


def test_unfilled_order_raises():
    x = make_x()
    empty = {"id": "4", "filled": 0.0, "average": None, "cost": 0.0}
    x._retry = lambda fn, *a, **k: empty  # neutralise le fetch_order de relecture
    with pytest.raises(ExchangeError):
        x._normalize_fill(empty, "BTC/USDT", "buy")


def test_orders_are_refused_without_trading_rights():
    with pytest.raises(ExchangeError):
        make_x().market_buy_quote("BTC/USDT", 20.0)
