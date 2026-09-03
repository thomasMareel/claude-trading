"""Couche de risque deterministe.

C'est le seul endroit qui a le droit de dire non. Elle ne connait pas les
cerveaux, elle ne lit que la config et l'etat du book. Chaque refus est
motive et journalise : un refus est une information, pas un echec.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .brains.base import BrainContext, Decision, PositionView
from .storage import Storage


def week_start_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.isoformat(timespec="seconds")


def day_start_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


class RiskManager:
    def __init__(self, cfg, storage: Storage):
        self.cfg = cfg
        self.storage = storage
        r = cfg.get("risk", {}) or {}
        self.max_position_pct = float(r.get("max_position_pct", 0.4))
        self.max_open = int(r.get("max_open_positions", 2))
        self.min_order_value = float(r.get("min_order_value", 12.0))
        self.max_rt_week = int(r.get("max_round_trips_per_week", 3))
        self.max_daily_loss_pct = float(r.get("max_daily_loss_pct", 0.06))
        self.kill_dd = float(r.get("kill_switch_drawdown_pct", 0.25))
        self.stop_loss_pct = float(r.get("stop_loss_pct", 0.08))
        self.take_profit_pct = float(r.get("take_profit_pct", 0.15))

    # ------------------------------------------------------------------
    def limits_for_prompt(self) -> dict:
        return {
            "max_position_pct": self.max_position_pct,
            "max_open_positions": self.max_open,
            "min_order_value": self.min_order_value,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
        }

    def round_trips_used(self, brain: str) -> int:
        return self.storage.round_trips_since(brain, week_start_iso())

    # ------------------------------------------------------------------
    def kill_switch(self, total_equity: float, initial_total: float) -> bool:
        """Vrai si le book total a perdu plus que le seuil. Definitif."""
        if self.storage.kill_switch_tripped():
            return True
        dd = 1 - total_equity / initial_total if initial_total > 0 else 0.0
        if dd >= self.kill_dd:
            self.storage.event(
                "critical", "kill_switch",
                f"COUPE-CIRCUIT : drawdown {dd*100:.1f}% >= {self.kill_dd*100:.0f}%. "
                f"Equity {total_equity:.2f} / initial {initial_total:.2f}. Arret definitif.",
                {"equity": total_equity, "initial": initial_total, "drawdown": dd},
            )
            return True
        return False

    def daily_loss_frozen(self, ctx: BrainContext) -> bool:
        return ctx.daily_pnl_pct <= -self.max_daily_loss_pct * 100

    # ------------------------------------------------------------------
    def forced_exits(self, ctx: BrainContext) -> list[tuple[PositionView, str]]:
        """Stop de perte et objectif de gain. Prioritaires sur tout cerveau."""
        out = []
        for p in ctx.positions:
            if p.stop_loss and p.current_price <= p.stop_loss:
                out.append((p, "stop_loss"))
            elif p.take_profit and p.current_price >= p.take_profit:
                out.append((p, "take_profit"))
        return out

    def stop_and_target(self, entry_price: float) -> tuple[float, float]:
        return (
            round(entry_price * (1 - self.stop_loss_pct), 8),
            round(entry_price * (1 + self.take_profit_pct), 8),
        )

    # ------------------------------------------------------------------
    def vet(
        self, ctx: BrainContext, d: Decision, *, min_notional: float, cash_now: float,
        open_now: int, opens_this_cycle: int,
    ) -> tuple[Decision | None, str | None]:
        """Retourne (decision eventuellement redimensionnee, motif de refus).

        cash_now / open_now / opens_this_cycle refletent l'etat APRES les
        decisions deja executees dans ce cycle, pour eviter qu'un cerveau
        depense deux fois le meme cash.
        """
        if d.action == "hold":
            return d, None

        pos = ctx.position_for(d.symbol)

        if d.action == "sell":
            if pos is None:
                return None, "vente refusee : aucune position ouverte sur ce symbole"
            return d, None

        # ---- buy ----
        if pos is not None:
            return None, "achat refuse : position deja ouverte, pas de renforcement"
        if self.daily_loss_frozen(ctx):
            return None, (f"achat refuse : perte du jour {ctx.daily_pnl_pct:.1f}% >= "
                          f"limite {self.max_daily_loss_pct*100:.0f}%, cerveau gele jusqu'a demain")
        if open_now >= self.max_open:
            return None, f"achat refuse : {open_now} positions ouvertes, maximum {self.max_open}"
        used = ctx.round_trips_used + opens_this_cycle
        if used >= self.max_rt_week:
            return None, (f"achat refuse : budget hebdo epuise ({used}/{self.max_rt_week} "
                          f"nouvelles positions depuis lundi)")

        wanted = float(d.size_quote or 0.0)
        cap = ctx.equity * self.max_position_pct
        size = min(wanted, cap, cash_now * 0.995)  # marge pour les frais
        floor = max(self.min_order_value, min_notional)
        if size < floor:
            return None, (f"achat refuse : taille {size:.2f} < minimum {floor:.2f} "
                          f"(demande {wanted:.2f}, cash {cash_now:.2f}, plafond {cap:.2f})")
        adjusted = Decision(
            d.symbol, "buy", size_quote=round(size, 2), confidence=d.confidence,
            reasoning=d.reasoning, raw=d.raw,
        )
        if abs(size - wanted) > 0.01:
            adjusted.raw = {**adjusted.raw, "resized_from": wanted}
        return adjusted, None
