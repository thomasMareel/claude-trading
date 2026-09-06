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
from src.exchange import Exchange, ExchangeError, FeesUnknownError, OrderUncertainError  # noqa: E402

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


def test_client_order_id_is_deterministic_and_venue_safe():
    """L'identifiant depend maintenant de la PLATEFORME : chacune impose son
    alphabet et sa longueur. C'est le correctif du defaut silencieux ou un
    identifiant au format Binance etait ignore ailleurs."""
    x = make_x()
    cid = x.client_order_id("20260904T091345Z-B-BTC/USDT")
    assert cid == "20260904T091345Z-B-BTCUSDT"       # Binance tolere le tiret, le slash part
    assert len(cid) <= 36
    assert x.client_order_id("20260904T091345Z-B-BTC/USDT") == cid   # meme tag, meme id
    assert x.client_order_id("WD20260904T091345Z-S-SOL/USDT") != cid


# ------------------------------------------------------------ chemin d'envoi, sans reseau
import ccxt  # noqa: E402


def make_live(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    monkeypatch.setattr("src.exchange.time.sleep", lambda *_: None)
    x = Exchange(CFG, trading=True)
    x.amount_to_precision = lambda symbol, amount: amount
    return x


FILLED = {"id": "42", "status": "closed", "filled": 0.0004, "average": 100_000.0, "cost": 40.0,
          "fees": [{"currency": "BTC", "cost": 0.0000004}]}


def test_operation_failed_on_send_is_recovered_by_client_order_id(monkeypatch):
    x = make_live(monkeypatch)
    seen = {}

    def create(*a, **k):
        raise ccxt.OperationFailed("binance -1006 Execution status unknown")

    def fetch_order(oid, symbol, params):
        seen["params"] = params
        return FILLED

    x._x.create_market_buy_order_with_cost = create
    x._x.fetch_order = fetch_order
    f = x.market_buy_quote("BTC/USDT", 40.0, tag="C1-B-BTC/USDT")
    assert seen["params"] == {"origClientOrderId": "C1-B-BTCUSDT"}
    assert f["amount_base"] == pytest.approx(0.0003996)      # l'ordre retrouve est pris tel quel


def test_order_never_received_is_a_clean_failure_not_uncertainty(monkeypatch):
    x = make_live(monkeypatch)
    x._x.create_market_buy_order_with_cost = lambda *a, **k: (_ for _ in ()).throw(ccxt.RequestTimeout("timeout"))

    def fetch_order(*a, **k):
        raise ccxt.OrderNotFound("binance -2013 Order does not exist")

    x._x.fetch_order = fetch_order
    with pytest.raises(ExchangeError) as ei:
        x.market_buy_quote("BTC/USDT", 40.0, tag="C2-B-BTC/USDT")
    assert not isinstance(ei.value, OrderUncertainError)
    assert "jamais recu" in str(ei.value)


def test_unreadable_recovery_is_uncertain(monkeypatch):
    x = make_live(monkeypatch)
    x._x.create_market_buy_order_with_cost = lambda *a, **k: (_ for _ in ()).throw(ccxt.NetworkError("down"))
    x._x.fetch_order = lambda *a, **k: (_ for _ in ()).throw(ccxt.NetworkError("still down"))
    with pytest.raises(OrderUncertainError):
        x.market_buy_quote("BTC/USDT", 40.0, tag="C3-B-BTC/USDT")


def test_clear_refusal_before_execution_is_a_clean_failure(monkeypatch):
    x = make_live(monkeypatch)
    x._x.create_market_buy_order_with_cost = lambda *a, **k: (_ for _ in ()).throw(ccxt.InsufficientFunds("-2010"))
    with pytest.raises(ExchangeError) as ei:
        x.market_buy_quote("BTC/USDT", 40.0, tag="C4-B-BTC/USDT")
    assert not isinstance(ei.value, OrderUncertainError)


def test_error_after_acceptance_is_uncertain_never_a_plain_failure(monkeypatch):
    x = make_live(monkeypatch)
    x._x.create_market_buy_order_with_cost = lambda *a, **k: {"id": "7", "status": "closed"}   # sans average ni filled
    x._x.fetch_order = lambda *a, **k: (_ for _ in ()).throw(ccxt.NetworkError("relecture impossible"))
    with pytest.raises(OrderUncertainError):
        x.market_buy_quote("BTC/USDT", 40.0, tag="C5-B-BTC/USDT")


NO_FEE = {"id": "9", "status": "closed", "filled": 0.0004, "average": 100_000.0, "cost": 40.0}


def _sans_frais_lisibles(monkeypatch, cfg=None):
    x = Exchange(cfg or CFG, trading=True) if cfg else make_live(monkeypatch)
    if cfg:
        x.amount_to_precision = lambda s, a: a
    x._x.create_market_buy_order_with_cost = lambda *a, **k: (_ for _ in ()).throw(ccxt.RequestTimeout("t"))
    x._x.fetch_order = lambda *a, **k: NO_FEE
    x._x.fetch_order_trades = lambda *a, **k: (_ for _ in ()).throw(ccxt.NetworkError("no trades"))
    return x


def test_frais_illisibles_refuses_plutot_qu_estimes_en_silence(monkeypatch):
    """Sur une marge de 2 %, des frais estimes faussent le prix de revient donc
    le prix de revente, sans que rien ne l'indique. On refuse bruyamment."""
    x = _sans_frais_lisibles(monkeypatch)
    with pytest.raises(FeesUnknownError, match="frais illisibles"):
        x.market_buy_quote("BTC/USDT", 40.0, tag="C6-B-BTC/USDT")


def test_frais_absents_sans_panne_reseau_refuses_aussi(monkeypatch):
    """Cas distinct : l'ordre et ses executions se lisent, mais aucun frais
    n'y figure. Refuse de la meme facon."""
    x = make_live(monkeypatch)
    x._x.create_market_buy_order_with_cost = lambda *a, **k: dict(NO_FEE)
    x._x.fetch_order = lambda *a, **k: dict(NO_FEE)
    x._x.fetch_order_trades = lambda *a, **k: []
    with pytest.raises(FeesUnknownError, match="aucun frais lisible"):
        x.market_buy_quote("BTC/USDT", 40.0, tag="C7-B-BTC/USDT")


def test_estimation_possible_mais_seulement_si_elle_est_demandee(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k"); monkeypatch.setenv("BINANCE_API_SECRET", "s")
    monkeypatch.setattr("src.exchange.time.sleep", lambda *_: None)
    cfg = Config(raw={"exchange": {**CFG.raw["exchange"], "autoriser_frais_estimes": True}})
    x = _sans_frais_lisibles(monkeypatch, cfg)
    f = x.market_buy_quote("BTC/USDT", 40.0, tag="C6-B-BTC/USDT")
    assert f["amount_base"] == pytest.approx(0.0004 * 0.999)   # prudent : la vente ne peut pas echouer
    assert f["fee_quote"] == pytest.approx(0.04)


def test_une_plateforme_sans_frais_lisibles_est_refusee_d_emblee(monkeypatch):
    monkeypatch.setenv("BITVAVO_API_KEY", "k"); monkeypatch.setenv("BITVAVO_API_SECRET", "s")
    cfg = Config(raw={"exchange": {"id": "bitvavo", "quote": "EUR", "fee_rate": 0.001}})
    x = Exchange(cfg, trading=True)
    with pytest.raises(FeesUnknownError, match="ne permet pas de relire"):
        x._fees_from_trades("9", "BTC/EUR")
