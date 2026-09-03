"""Contrat commun aux deux cerveaux.

Un cerveau recoit un BrainContext (tout ce qu'il a le droit de savoir)
et rend une liste de Decision. Il ne touche jamais a l'exchange, jamais
a la base, jamais au capital. C'est le moteur qui execute, apres passage
par la couche de risque.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import pandas as pd

Action = Literal["buy", "sell", "hold"]


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    df: pd.DataFrame                       # bougies enrichies d'indicateurs
    indicators: dict[str, Any]             # dernieres valeurs, lisibles


@dataclass
class PositionView:
    position_id: int
    symbol: str
    entry_price: float
    amount_base: float
    cost_quote: float
    current_price: float
    opened_at: str
    stop_loss: float | None
    take_profit: float | None

    @property
    def value_quote(self) -> float:
        return self.amount_base * self.current_price

    @property
    def pnl_quote(self) -> float:
        return self.value_quote - self.cost_quote

    @property
    def pnl_pct(self) -> float:
        return (self.current_price / self.entry_price - 1) * 100 if self.entry_price else 0.0


@dataclass
class BrainContext:
    cycle_id: str
    brain: str
    now_iso: str
    initial_capital: float
    cash: float
    positions: list[PositionView]
    markets: dict[str, MarketSnapshot]
    recent_decisions: list[dict[str, Any]]
    recent_trades: list[dict[str, Any]]
    round_trips_used: int
    round_trips_budget: int
    daily_pnl_pct: float
    fee_rate: float
    limits: dict[str, Any] = field(default_factory=dict)

    @property
    def positions_value(self) -> float:
        return sum(p.value_quote for p in self.positions)

    @property
    def equity(self) -> float:
        return self.cash + self.positions_value

    def position_for(self, symbol: str) -> PositionView | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None


@dataclass
class Decision:
    symbol: str
    action: Action
    size_quote: float | None = None        # montant a engager (buy uniquement)
    confidence: float = 0.5
    reasoning: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class Brain(Protocol):
    name: str

    def decide(self, ctx: BrainContext) -> list[Decision]: ...
