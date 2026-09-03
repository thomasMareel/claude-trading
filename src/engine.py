"""Le moteur : un cycle de decision complet, identique en paper et en live.

Ordre d'un cycle :
  1. rafraichir les bougies et les prix
  2. coupe-circuit global (drawdown du book total)
  3. pour chaque cerveau :
       a. sorties forcees (stop de perte, objectif de gain)
       b. construction du dossier (BrainContext)
       c. le cerveau decide
       d. la couche de risque tranche, decision par decision
       e. execution des ventes, puis des achats
       f. releve d'equity
  4. resume console
"""
from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .brains.base import Brain, BrainContext, Decision, MarketSnapshot, PositionView
from .config import Config
from .exchange import Exchange
from .executor import LiveExecutor, PaperExecutor
from .indicators import candles_to_df, enrich, latest_snapshot
from .portfolio import (
    build_positions, compute_cash, equity_day_start,
    recent_decisions_view, recent_trades_view,
)
from .risk import RiskManager
from .storage import Storage, utcnow_iso

console = Console()


class Engine:
    def __init__(
        self, cfg: Config, storage: Storage, data: Exchange,
        executor: PaperExecutor | LiveExecutor, brains: dict[str, Brain], risk: RiskManager,
    ):
        self.cfg = cfg
        self.storage = storage
        self.data = data
        self.executor = executor
        self.brains = brains
        self.risk = risk
        self.symbols = cfg.symbols
        self.timeframe = cfg.timeframe
        self.lookback = int(cfg.get("exchange.lookback_candles", 300))
        self.fee_rate = float(cfg.get("exchange.fee_rate", 0.001))
        self.ind_params = dict(
            ema_fast=int(cfg.get("rules.ema_fast", 50)),
            ema_slow=int(cfg.get("rules.ema_slow", 200)),
            rsi_period=int(cfg.get("rules.rsi_period", 14)),
            atr_period=int(cfg.get("rules.atr_period", 14)),
        )

    # ==================================================================
    def run_cycle(self) -> str:
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        console.rule(f"[bold]Cycle {cycle_id}  mode={self.executor.mode}")

        markets = self._refresh_markets()
        prices = {s: m.price for s, m in markets.items()}

        # ---- coupe-circuit global ----
        total_equity, initial_total = 0.0, 0.0
        for name in self.brains:
            init = self.cfg.brain_capital(name)
            cash = compute_cash(self.storage, name, init)
            pos = build_positions(self.storage, name, prices)
            total_equity += cash + sum(p.value_quote for p in pos)
            initial_total += init
        if self.risk.kill_switch(total_equity, initial_total):
            console.print("[bold red]COUPE-CIRCUIT ACTIF : liquidation et arret.[/]")
            self._liquidate_all(cycle_id, prices)
            self._summary(cycle_id, prices)
            return cycle_id

        # ---- chaque cerveau ----
        for name, brain in self.brains.items():
            self._run_brain(cycle_id, name, brain, markets, prices)

        self._summary(cycle_id, prices)
        return cycle_id

    # ==================================================================
    def _refresh_markets(self) -> dict[str, MarketSnapshot]:
        out: dict[str, MarketSnapshot] = {}
        prices = self.data.fetch_prices(self.symbols)
        for s in self.symbols:
            rows = self.data.fetch_ohlcv(s, self.timeframe, limit=self.lookback)
            self.storage.upsert_candles(s, self.timeframe, rows)
            candles = self.storage.candles(s, self.timeframe, limit=self.lookback)
            df = enrich(candles_to_df(candles), **self.ind_params)
            # la derniere bougie est en cours : on la remplace par le prix live
            df.loc[df.index[-1], "close"] = prices[s]
            out[s] = MarketSnapshot(symbol=s, price=prices[s], df=df, indicators=latest_snapshot(df))
        return out

    def _context(self, cycle_id: str, name: str, markets, prices) -> BrainContext:
        init = self.cfg.brain_capital(name)
        cash = compute_cash(self.storage, name, init)
        positions = build_positions(self.storage, name, prices)
        equity = cash + sum(p.value_quote for p in positions)
        ref = equity_day_start(self.storage, name, init)
        daily_pct = (equity / ref - 1) * 100 if ref > 0 else 0.0
        return BrainContext(
            cycle_id=cycle_id, brain=name, now_iso=utcnow_iso(),
            initial_capital=init, cash=cash, positions=positions, markets=markets,
            recent_decisions=recent_decisions_view(self.storage, name),
            recent_trades=recent_trades_view(self.storage, name),
            round_trips_used=self.risk.round_trips_used(name),
            round_trips_budget=self.risk.max_rt_week,
            daily_pnl_pct=daily_pct, fee_rate=self.fee_rate,
            limits=self.risk.limits_for_prompt(),
        )

    # ==================================================================
    def _run_brain(self, cycle_id: str, name: str, brain: Brain, markets, prices) -> None:
        console.print(f"\n[bold cyan]>> cerveau {name}[/]")

        # a. sorties forcees
        ctx = self._context(cycle_id, name, markets, prices)
        for p, reason in self.risk.forced_exits(ctx):
            did = self.storage.record_decision(
                cycle_id, name, p.symbol, "sell", confidence=1.0,
                reasoning=f"sortie forcee par la couche de risque : {reason} "
                          f"(prix {p.current_price}, entree {p.entry_price}, "
                          f"stop {p.stop_loss}, objectif {p.take_profit})",
                accepted=True, raw={"forced": reason},
            )
            self._execute_sell(cycle_id, name, p, prices[p.symbol], reason, did)

        # b. dossier a jour
        ctx = self._context(cycle_id, name, markets, prices)

        # c. decision
        try:
            decisions = brain.decide(ctx)
        except Exception as e:  # un cerveau qui plante ne doit pas tuer le cycle
            self.storage.event("warning", f"brain_{name}", f"exception dans decide(): {e!r}")
            console.print(f"[red]cerveau {name} en erreur : {e!r} -> hold[/]")
            decisions = [Decision(s, "hold", reasoning=f"erreur cerveau : {e!r}") for s in markets]

        # d+e. tri : ventes d'abord pour liberer le cash, puis achats, puis holds
        order = {"sell": 0, "buy": 1, "hold": 2}
        decisions.sort(key=lambda d: order.get(d.action, 3))

        cash_now = ctx.cash
        open_now = len(ctx.positions)
        opens_this_cycle = 0
        for d in decisions:
            try:
                min_notional = self.data.min_notional(d.symbol)
            except Exception:
                min_notional = 5.0
            vetted, why = self.risk.vet(
                ctx, d, min_notional=min_notional, cash_now=cash_now,
                open_now=open_now, opens_this_cycle=opens_this_cycle,
            )
            accepted = vetted is not None
            did = self.storage.record_decision(
                cycle_id, name, d.symbol, d.action,
                size_quote=(vetted.size_quote if vetted else d.size_quote),
                confidence=d.confidence, reasoning=d.reasoning,
                accepted=accepted, reject_reason=why, raw=(vetted.raw if vetted else d.raw),
            )
            tag = "[green]OK[/]" if accepted else "[yellow]REFUS[/]"
            console.print(f"  {d.symbol:<9} {d.action:<5} {tag}  {(why or d.reasoning)[:110]}")
            if not accepted or vetted is None or vetted.action == "hold":
                continue

            if vetted.action == "sell":
                p = ctx.position_for(d.symbol)
                if p is None:
                    continue
                fill = self._execute_sell(cycle_id, name, p, prices[d.symbol], "signal", did)
                if fill:
                    cash_now += fill.value_quote - fill.fee_quote
                    open_now -= 1
            elif vetted.action == "buy":
                fill = self._execute_buy(cycle_id, name, vetted, prices[d.symbol], did)
                if fill:
                    cash_now -= fill.value_quote + fill.fee_quote
                    open_now += 1
                    opens_this_cycle += 1

        # f. releve
        final = self._context(cycle_id, name, markets, prices)
        self.storage.record_equity(name, final.cash, final.positions_value)

    # ==================================================================
    def _execute_buy(self, cycle_id, name, d: Decision, price: float, decision_id: int):
        try:
            fill = self.executor.buy(d.symbol, float(d.size_quote), price)
        except Exception as e:
            self.storage.event("warning", "executor", f"achat {name} {d.symbol} echoue : {e!r}")
            console.print(f"    [red]execution achat echouee : {e!r}[/]")
            return None
        stop, target = self.risk.stop_and_target(fill.price)
        self.storage.record_order(
            cycle_id, name, d.symbol, "buy", self.executor.mode, fill.price,
            fill.amount_base, fill.value_quote, fill.fee_quote, fill.exchange_id, decision_id,
        )
        self.storage.open_position(
            name, d.symbol, fill.price, fill.amount_base,
            cost_quote=fill.value_quote + fill.fee_quote, fees_quote=fill.fee_quote,
            stop_loss=stop, take_profit=target,
        )
        console.print(f"    [green]ACHAT[/] {fill.amount_base:.6f} @ {fill.price:.4f} "
                      f"= {fill.value_quote:.2f} (frais {fill.fee_quote:.3f}) "
                      f"stop {stop:.4f} / objectif {target:.4f}")
        return fill

    def _execute_sell(self, cycle_id, name, p: PositionView, price: float, reason: str, decision_id: int):
        try:
            fill = self.executor.sell(p.symbol, p.amount_base, price)
        except Exception as e:
            self.storage.event("warning", "executor", f"vente {name} {p.symbol} echouee : {e!r}")
            console.print(f"    [red]execution vente echouee : {e!r}[/]")
            return None
        self.storage.record_order(
            cycle_id, name, p.symbol, "sell", self.executor.mode, fill.price,
            fill.amount_base, fill.value_quote, fill.fee_quote, fill.exchange_id, decision_id,
        )
        pnl = self.storage.close_position(
            p.position_id, fill.price, proceeds_quote=fill.value_quote - fill.fee_quote,
            fee_quote=fill.fee_quote, reason=reason,
        )
        color = "green" if pnl >= 0 else "red"
        console.print(f"    [{color}]VENTE[/] {fill.amount_base:.6f} @ {fill.price:.4f} "
                      f"= {fill.value_quote:.2f} (frais {fill.fee_quote:.3f}) "
                      f"PnL {pnl:+.2f} [{reason}]")
        return fill

    def _liquidate_all(self, cycle_id: str, prices: dict[str, float]) -> None:
        for name in self.brains:
            for p in build_positions(self.storage, name, prices):
                did = self.storage.record_decision(
                    cycle_id, name, p.symbol, "sell", confidence=1.0,
                    reasoning="liquidation : coupe-circuit global", accepted=True,
                    raw={"forced": "kill_switch"},
                )
                self._execute_sell(cycle_id, name, p, prices[p.symbol], "kill_switch", did)
            ctx_cash = compute_cash(self.storage, name, self.cfg.brain_capital(name))
            self.storage.record_equity(name, ctx_cash, 0.0)

    # ==================================================================
    def _summary(self, cycle_id: str, prices: dict[str, float]) -> None:
        t = Table(title=f"Etat des books apres {cycle_id}", show_lines=False)
        for col in ("cerveau", "cash", "positions", "equity", "perf", "trades", "frais"):
            t.add_column(col, justify="right" if col != "cerveau" else "left")
        for name in self.brains:
            init = self.cfg.brain_capital(name)
            cash = compute_cash(self.storage, name, init)
            pos = build_positions(self.storage, name, prices)
            pv = sum(p.value_quote for p in pos)
            eq = cash + pv
            perf = (eq / init - 1) * 100
            n_trades = len(self.storage.closed_positions(name))
            fees = self.storage.total_fees(name)
            color = "green" if perf >= 0 else "red"
            t.add_row(name, f"{cash:.2f}", f"{pv:.2f} ({len(pos)})", f"{eq:.2f}",
                      f"[{color}]{perf:+.2f}%[/]", str(n_trades), f"{fees:.3f}")
        console.print(t)
        api = self.storage.api_cost_total()
        console.print(f"[dim]cout API cumule : {api:.3f} $  |  aujourd'hui : "
                      f"{self.storage.api_cost_today():.3f} $[/]")


# ======================================================================
def build_engine(cfg: Config, *, mode_override: str | None = None) -> Engine:
    """Assemble le moteur selon config.yaml. Le live exige LIVE_ARMED."""
    from .config import live_is_armed
    from .brains.llm_brain import LLMBrain
    from .brains.rules_brain import RulesBrain

    mode = (mode_override or cfg.mode).lower()
    storage = Storage(cfg.get("storage.db_path"), cfg.get("storage.journal_path"))
    testnet = bool(cfg.get("engine.use_testnet", False))

    if mode == "live":
        if not live_is_armed():
            raise SystemExit(
                "Mode live demande mais le fichier LIVE_ARMED est absent.\n"
                "Cree-le a la main a la racine du projet quand tu es pret :\n"
                "    echo armed > LIVE_ARMED\n"
                "C'est la seconde serrure, volontaire."
            )
        data = Exchange(cfg, trading=True, testnet=testnet)
        executor = LiveExecutor(data)
        storage.event("info", "engine", f"demarrage LIVE (testnet={testnet})")
    else:
        data = Exchange(cfg, trading=False)
        executor = PaperExecutor(
            fee_rate=float(cfg.get("exchange.fee_rate", 0.001)),
            slippage=float(cfg.get("exchange.slippage", 0.0005)),
            amount_precision=data.amount_to_precision,
        )

    brains: dict[str, Brain] = {}
    alloc = cfg.get("experiment.allocation", {}) or {}
    if float(alloc.get("llm", 0)) > 0:
        brains["llm"] = LLMBrain(cfg, storage)
    if float(alloc.get("rules", 0)) > 0:
        brains["rules"] = RulesBrain(cfg)

    risk = RiskManager(cfg, storage)
    return Engine(cfg, storage, data, executor, brains, risk)
