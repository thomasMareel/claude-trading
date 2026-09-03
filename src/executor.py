"""Execution des ordres : simulation (paper) ou reel (live).

Les deux executeurs partagent exactement la meme interface, pour que le
moteur soit identique en paper et en live. C'est la condition pour que
deux semaines de paper trading valident vraiment le code qui partira
en reel.
"""
from __future__ import annotations

from dataclasses import dataclass

from .exchange import Exchange


@dataclass
class Fill:
    price: float
    amount_base: float
    value_quote: float
    fee_quote: float
    exchange_id: str | None = None


class PaperExecutor:
    mode = "paper"

    def __init__(self, fee_rate: float, slippage: float, amount_precision):
        self.fee_rate = fee_rate
        self.slippage = slippage
        self._prec = amount_precision  # fn(symbol, amount) -> amount arrondi

    def buy(self, symbol: str, size_quote: float, price: float) -> Fill:
        px = price * (1 + self.slippage)
        raw_amount = size_quote / px
        amount = self._prec(symbol, raw_amount)
        value = amount * px
        return Fill(price=px, amount_base=amount, value_quote=value, fee_quote=value * self.fee_rate)

    def sell(self, symbol: str, amount_base: float, price: float) -> Fill:
        px = price * (1 - self.slippage)
        amount = self._prec(symbol, amount_base)
        value = amount * px
        return Fill(price=px, amount_base=amount, value_quote=value, fee_quote=value * self.fee_rate)


class LiveExecutor:
    mode = "live"

    def __init__(self, exchange: Exchange):
        if not exchange.trading:
            raise RuntimeError("LiveExecutor exige un Exchange construit avec trading=True")
        self.x = exchange

    def buy(self, symbol: str, size_quote: float, price: float) -> Fill:
        f = self.x.market_buy_quote(symbol, size_quote)
        return Fill(**f)

    def sell(self, symbol: str, amount_base: float, price: float) -> Fill:
        f = self.x.market_sell_base(symbol, amount_base)
        return Fill(**f)
