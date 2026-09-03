"""Reconstruction du book d'un cerveau a partir du journal.

Le cash n'est jamais stocke : il est recalcule depuis les ordres. Ainsi
la base reste la seule source de verite et un crash en plein cycle ne
peut pas laisser un solde fantome.
"""
from __future__ import annotations

from .brains.base import PositionView
from .risk import day_start_iso
from .storage import Storage


def compute_cash(storage: Storage, brain: str, initial_capital: float) -> float:
    cash = initial_capital
    for o in storage.orders_for(brain):
        if o["side"] == "buy":
            cash -= float(o["value_quote"]) + float(o["fee_quote"])
        else:
            cash += float(o["value_quote"]) - float(o["fee_quote"])
    return cash


def build_positions(storage: Storage, brain: str, prices: dict[str, float]) -> list[PositionView]:
    out = []
    for r in storage.open_positions(brain):
        px = prices.get(r["symbol"])
        if px is None:
            continue
        out.append(PositionView(
            position_id=int(r["id"]), symbol=r["symbol"],
            entry_price=float(r["entry_price"]), amount_base=float(r["amount_base"]),
            cost_quote=float(r["cost_quote"]), current_price=px,
            opened_at=r["opened_at"], stop_loss=r["stop_loss"], take_profit=r["take_profit"],
        ))
    return out


def equity_day_start(storage: Storage, brain: str, fallback: float) -> float:
    """Equity de reference pour la perte journaliere : dernier releve
    d'hier, sinon premier releve d'aujourd'hui, sinon le capital initial."""
    today = day_start_iso()
    curve = storage.equity_curve(brain)
    before = [r for r in curve if r["ts"] < today]
    if before:
        return float(before[-1]["total_quote"])
    today_rows = [r for r in curve if r["ts"] >= today]
    if today_rows:
        return float(today_rows[0]["total_quote"])
    return fallback


def recent_decisions_view(storage: Storage, brain: str, n: int = 8) -> list[dict]:
    out = []
    for r in storage.recent_decisions(brain, n):
        out.append({
            "ts": r["ts"], "symbol": r["symbol"], "action": r["action"],
            "acceptee": bool(r["accepted"]),
            "refus": r["reject_reason"],
            "raison": (r["reasoning"] or "")[:240],
        })
    return out


def recent_trades_view(storage: Storage, brain: str, n: int = 6) -> list[dict]:
    rows = storage.closed_positions(brain)[-n:]
    return [
        {
            "symbol": r["symbol"], "entree": r["entry_price"], "sortie": r["exit_price"],
            "pnl_quote": round(float(r["pnl_quote"] or 0), 2),
            "pnl_pct": round((float(r["exit_price"]) / float(r["entry_price"]) - 1) * 100, 2)
            if r["exit_price"] else None,
            "frais": round(float(r["fees_quote"]), 3),
            "motif_cloture": r["close_reason"], "ouverte": r["opened_at"], "fermee": r["closed_at"],
        }
        for r in rows
    ]
