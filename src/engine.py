"""Le moteur : un cycle de decision complet, identique en paper et en live.

Un seul trader, Claude. Un seul repere : le panier equipondere des memes
actifs, achete au premier cycle et jamais touche, aux memes frais et au
meme slippage. Claude doit faire mieux que ce repere, net de tout.

Ordre d'un cycle :
  1. rafraichir les bougies et les prix
  2. releve du repere buy-and-hold (constitue au premier cycle)
  3. coupe-circuit (drawdown du book, garde-fou de catastrophe)
  4. sorties forcees (stop de perte, objectif de gain)
  5. construction du dossier (BrainContext)
  6. Claude decide
  7. la couche de risque tranche, decision par decision
  8. execution des ventes, puis des achats
  9. releve d'equity, resume console
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .alerts import notify
from .brains.base import Brain, BrainContext, Decision, MarketSnapshot, PositionView
from .config import Config
from .exchange import Exchange, OrderUncertainError
from .executor import LiveExecutor, PaperExecutor
from .indicators import candles_to_df, enrich, latest_snapshot
from .portfolio import (
    build_positions, compute_cash, equity_day_start,
    recent_decisions_view, recent_trades_view,
)
from .risk import BOOK_UNCERTAIN, RiskManager
from .storage import Storage, utcnow_iso

console = Console()
BENCHMARK = "benchmark"


def timeframe_to_ms(tf: str) -> int:
    unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[tf[-1]]
    return int(tf[:-1]) * unit * 1000


class Engine:
    def __init__(
        self, cfg: Config, storage: Storage, data: Exchange,
        executor: PaperExecutor | LiveExecutor, brain: Brain, risk: RiskManager,
    ):
        self.cfg = cfg
        self.storage = storage
        self.data = data
        self.executor = executor
        self.brain = brain
        self.name = brain.name
        self.risk = risk
        self.symbols = cfg.symbols
        self.timeframe = cfg.timeframe
        self.capital = cfg.total_capital
        self.quote = str(cfg.get("exchange.quote", "USDT"))
        self.lookback = int(cfg.get("exchange.lookback_candles", 300))
        self.fee_rate = float(cfg.get("exchange.fee_rate", 0.001))
        self.slippage = float(cfg.get("exchange.slippage", 0.0005))
        self.alerts_on = bool(cfg.get("alerts.enabled", True))
        self.alert_forced = bool(cfg.get("alerts.on_forced_exit", True))
        self.ind_params = dict(
            ema_fast=int(cfg.get("indicators.ema_fast", 50)),
            ema_slow=int(cfg.get("indicators.ema_slow", 200)),
            rsi_period=int(cfg.get("indicators.rsi_period", 14)),
            atr_period=int(cfg.get("indicators.atr_period", 14)),
        )

    # ==================================================================
    def _alert(self, title: str, message: str, priority: str = "default", tags: str = "") -> None:
        if self.alerts_on:
            notify(title, message, priority=priority, tags=tags)

    def _book(self, prices: dict[str, float]) -> tuple[float, list[PositionView]]:
        cash = compute_cash(self.storage, self.name, self.capital)
        positions = build_positions(self.storage, self.name, prices)
        return cash, positions

    # ==================================================================
    def run_cycle(self) -> str:
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        console.rule(f"[bold]Cycle {cycle_id}  mode={self.executor.mode}")

        markets = self._refresh_markets()
        prices = {s: m.price for s, m in markets.items()}

        cash, positions = self._book(prices)
        equity = cash + sum(p.value_quote for p in positions)
        if self.risk.kill_switch(equity, self.capital):
            console.print("[bold red]COUPE-CIRCUIT ACTIF : liquidation et arret.[/]")
            self._alert("COUPE-CIRCUIT", f"Equity {equity:.2f} / {self.capital:.2f} {self.quote}. "
                        "Liquidation et arret definitif.", "urgent", "rotating_light")
            self._liquidate_all(cycle_id, prices)
            self._update_benchmark(prices)
            self._summary(cycle_id, prices)
            return cycle_id

        self._run_brain(cycle_id, markets, prices)
        # apres le cerveau : le repere n'est constitue qu'au premier cycle ou
        # Claude a vraiment repondu (t0 du protocole), aux prix de ce cycle
        self._update_benchmark(prices)
        self._summary(cycle_id, prices)
        return cycle_id

    # ==================================================================
    def _refresh_markets(self) -> dict[str, MarketSnapshot]:
        """Indicateurs sur bougies CLOTUREES uniquement.

        Au moment du cycle, la bougie en cours n'a que quelques minutes de
        vie : la garder ferait osciller les signaux d'un cycle a l'autre.
        Le prix live sert a l'execution, aux stops et au dossier de Claude.
        """
        out: dict[str, MarketSnapshot] = {}
        prices = self.data.fetch_prices(self.symbols)
        tf_ms = timeframe_to_ms(self.timeframe)
        now_ms = int(time.time() * 1000)
        for s in self.symbols:
            rows = self.data.fetch_ohlcv(s, self.timeframe, limit=self.lookback + 1)
            self.storage.upsert_candles(s, self.timeframe, rows)
            candles = self.storage.candles(s, self.timeframe, limit=self.lookback + 1)
            closed = [c for c in candles if int(c["ts"]) + tf_ms <= now_ms][-self.lookback:]
            df = enrich(candles_to_df(closed), **self.ind_params)
            out[s] = MarketSnapshot(symbol=s, price=prices[s], df=df, indicators=latest_snapshot(df))
        return out

    # ==================================================================
    def _update_benchmark(self, prices: dict[str, float]) -> float | None:
        """Le repere : panier equipondere achete a t0, jamais touche. Frais et
        slippage a l'entree ET a la sortie (valeur de liquidation), exactement
        comme le book de Claude.

        t0 est le premier cycle ou le trader a VRAIMENT repondu (une ligne dans
        api_costs). Avant cela, aucun repere : une experience qui n'a pas
        commence ne doit pas avoir de courbe de reference.
        """
        basket = self.storage.benchmark_basket()
        if not basket:
            if getattr(self.brain, "requires_api", False) and self.storage.api_calls_total() == 0:
                console.print("[dim]repere non constitue : le trader n'a encore jamais repondu, "
                              "t0 attend le premier appel API reussi[/]")
                return None
            per = self.capital / len(self.symbols)
            rows = []
            for s in self.symbols:
                entry_px = prices[s] * (1 + self.slippage)
                amount = per * (1 - self.fee_rate) / entry_px
                rows.append((s, utcnow_iso(), prices[s], amount, per))
            self.storage.set_benchmark_basket(rows)
            basket = self.storage.benchmark_basket()
            self.storage.event(
                "info", "protocol_start",
                f"t0 : repere constitue, {self.capital:.2f} {self.quote} en {len(self.symbols)} parts egales",
                {"capital": self.capital, "prices": prices, "symbols": self.symbols},
            )
            console.print(f"[bold]t0[/] repere buy-and-hold constitue : {per:.2f} {self.quote} "
                          f"sur chacun de {', '.join(self.symbols)}")
            self._alert("Experience demarree (t0)",
                        f"Repere constitue a " + ", ".join(f"{s.split('/')[0]} {p:.2f}" for s, p in prices.items()),
                        "default", "checkered_flag")
        gross = 0.0
        for r in basket:
            px = prices.get(r["symbol"], float(r["start_price"]))
            gross += float(r["amount_base"]) * px * (1 - self.slippage)
        value = gross * (1 - self.fee_rate)
        self.storage.record_equity(BENCHMARK, 0.0, value)
        return value

    def benchmark_value(self, prices: dict[str, float]) -> float | None:
        basket = self.storage.benchmark_basket()
        if not basket:
            return None
        gross = sum(float(r["amount_base"]) * prices.get(r["symbol"], float(r["start_price"]))
                    * (1 - self.slippage) for r in basket)
        return gross * (1 - self.fee_rate)

    # ==================================================================
    def check_stops(self) -> int:
        """Chien de garde entre deux cycles : stops, objectifs, coupe-circuit.

        N'appelle pas Claude et ne coute rien. Tolere l'absence de prix sur
        une paire : les autres sont quand meme verifiees. Retourne le nombre
        de positions fermees.
        """
        prices = self.data.fetch_prices(self.symbols, strict=False)
        missing = [s for s in self.symbols if s not in prices]
        if missing:
            console.print(f"[dim]chien de garde : pas de prix pour {missing}, "
                          f"positions correspondantes non verifiees ce tour[/]")
        cycle_id = "WD" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        cash, positions = self._book(prices)
        if not missing:
            equity = cash + sum(p.value_quote for p in positions)
            if self.risk.kill_switch(equity, self.capital):
                console.print("[bold red]COUPE-CIRCUIT (chien de garde) : liquidation.[/]")
                self._alert("COUPE-CIRCUIT", f"Equity {equity:.2f} / {self.capital:.2f} {self.quote}. "
                            "Liquidation et arret definitif.", "urgent", "rotating_light")
                n_before = len(positions)
                self._liquidate_all(cycle_id, prices)
                return n_before

        n = 0
        exits = self.risk.forced_exits(positions)
        for p, reason in exits:
            did = self.storage.record_decision(
                cycle_id, self.name, p.symbol, "sell", confidence=1.0,
                reasoning=f"chien de garde : {reason} (prix {p.current_price}, "
                          f"entree {p.entry_price}, stop {p.stop_loss}, objectif {p.take_profit})",
                accepted=True, raw={"forced": reason, "watchdog": True},
            )
            if self._execute_sell(cycle_id, p, prices[p.symbol], reason, did):
                n += 1
        if exits:
            cash, positions = self._book(prices)
            self.storage.record_equity(self.name, cash, sum(q.value_quote for q in positions))
        return n

    # ==================================================================
    def _context(self, cycle_id: str, markets, prices) -> BrainContext:
        cash, positions = self._book(prices)
        equity = cash + sum(p.value_quote for p in positions)
        ref = equity_day_start(self.storage, self.name, self.capital)
        daily_pct = (equity / ref - 1) * 100 if ref > 0 else 0.0
        return BrainContext(
            cycle_id=cycle_id, brain=self.name, now_iso=utcnow_iso(),
            initial_capital=self.capital, cash=cash, positions=positions, markets=markets,
            recent_decisions=recent_decisions_view(self.storage, self.name),
            recent_trades=recent_trades_view(self.storage, self.name),
            round_trips_used=self.risk.round_trips_used(self.name),
            round_trips_budget=self.risk.max_rt_week,
            daily_pnl_pct=daily_pct, fee_rate=self.fee_rate,
            limits=self.risk.limits_for_prompt(),
        )

    def _run_brain(self, cycle_id: str, markets, prices) -> None:
        console.print(f"\n[bold cyan]>> {self.name}[/]")

        # sorties forcees, avant toute decision
        ctx = self._context(cycle_id, markets, prices)
        for p, reason in self.risk.forced_exits(ctx.positions):
            did = self.storage.record_decision(
                cycle_id, self.name, p.symbol, "sell", confidence=1.0,
                reasoning=f"sortie forcee par la couche de risque : {reason} "
                          f"(prix {p.current_price}, entree {p.entry_price}, "
                          f"stop {p.stop_loss}, objectif {p.take_profit})",
                accepted=True, raw={"forced": reason},
            )
            self._execute_sell(cycle_id, p, prices[p.symbol], reason, did)

        # dossier a jour, puis decision
        ctx = self._context(cycle_id, markets, prices)
        if self.risk.daily_loss_frozen(ctx) and not self.storage.has_event_today("daily_freeze"):
            self.storage.event("warning", "daily_freeze",
                               f"perte du jour {ctx.daily_pnl_pct:.1f}% : achats geles jusqu'a demain",
                               {"daily_pnl_pct": ctx.daily_pnl_pct, "equity": ctx.equity})
            self._alert("Achats geles pour la journee", f"Perte du jour {ctx.daily_pnl_pct:.1f} %.", "high", "warning")
        try:
            decisions = self.brain.decide(ctx)
        except Exception as e:  # le cerveau qui plante ne doit pas tuer le cycle
            self.storage.event("warning", f"brain_{self.name}", f"exception dans decide(): {e!r}")
            console.print(f"[red]{self.name} en erreur : {e!r} -> hold[/]")
            decisions = [Decision(s, "hold", reasoning=f"erreur cerveau : {e!r}") for s in markets]

        # ventes d'abord pour liberer le cash, puis achats, puis holds
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
                cycle_id, self.name, d.symbol, d.action,
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
                fill = self._execute_sell(cycle_id, p, prices[d.symbol], "signal", did)
                if fill:
                    cash_now += fill.value_quote - fill.fee_quote
                    open_now -= 1
            elif vetted.action == "buy":
                fill = self._execute_buy(cycle_id, vetted, prices[d.symbol], did)
                if fill:
                    cash_now -= fill.value_quote + fill.fee_quote
                    open_now += 1
                    opens_this_cycle += 1

        final = self._context(cycle_id, markets, prices)
        self.storage.record_equity(self.name, final.cash, final.positions_value)

    # ==================================================================
    def _tag(self, cycle_id: str, side: str, symbol: str) -> str:
        return f"{cycle_id}-{side}-{symbol.replace('/', '')}"

    def _book_uncertain(self, side: str, symbol: str, err: Exception) -> None:
        """Un ordre reel a ete envoye et son resultat est inconnu. Le book
        ne peut plus etre cru : on gele les achats jusqu'a acquittement."""
        msg = (f"{side} {symbol} : ordre reel envoye, resultat INCONNU ({err}). "
               f"Le book n'est plus fiable. Achats geles jusqu'a verification manuelle "
               f"du compte Binance et acquittement (scripts/acquitter.py).")
        self.storage.event("critical", BOOK_UNCERTAIN, msg, {"side": side, "symbol": symbol})
        console.print(f"    [bold red]BOOK INCERTAIN[/] {msg}")
        self._alert("Book incertain", msg, "urgent", "warning")

    def _execute_buy(self, cycle_id: str, d: Decision, price: float, decision_id: int):
        try:
            fill = self.executor.buy(d.symbol, float(d.size_quote), price,
                                     tag=self._tag(cycle_id, "B", d.symbol))
        except OrderUncertainError as e:
            self._book_uncertain("achat", d.symbol, e)
            return None
        except Exception as e:
            self.storage.event("warning", "executor", f"achat {d.symbol} echoue : {e!r}")
            console.print(f"    [red]execution achat echouee : {e!r}[/]")
            return None
        stop, target = self.risk.stop_and_target(fill.price)
        self.storage.record_buy_fill(
            cycle_id=cycle_id, brain=self.name, symbol=d.symbol, mode=self.executor.mode,
            price=fill.price, amount_base=fill.amount_base, value_quote=fill.value_quote,
            fee_quote=fill.fee_quote, exchange_id=fill.exchange_id, decision_id=decision_id,
            stop_loss=stop, take_profit=target,
        )
        console.print(f"    [green]ACHAT[/] {fill.amount_base:.6f} @ {fill.price:.4f} "
                      f"= {fill.value_quote:.2f} (frais {fill.fee_quote:.3f}) "
                      f"stop {stop:.4f} / objectif {target:.4f}")
        return fill

    def _execute_sell(self, cycle_id: str, p: PositionView, price: float, reason: str, decision_id: int):
        try:
            fill = self.executor.sell(p.symbol, p.amount_base, price,
                                      tag=self._tag(cycle_id, "S", p.symbol))
        except OrderUncertainError as e:
            self._book_uncertain("vente", p.symbol, e)
            return None
        except Exception as e:
            self.storage.event("warning", "executor", f"vente {p.symbol} echouee : {e!r}")
            console.print(f"    [red]execution vente echouee : {e!r}[/]")
            return None
        pnl = self.storage.record_sell_fill(
            cycle_id=cycle_id, brain=self.name, symbol=p.symbol, mode=self.executor.mode,
            price=fill.price, amount_base=fill.amount_base, value_quote=fill.value_quote,
            fee_quote=fill.fee_quote, exchange_id=fill.exchange_id, decision_id=decision_id,
            position_id=p.position_id, reason=reason,
        )
        color = "green" if pnl >= 0 else "red"
        console.print(f"    [{color}]VENTE[/] {fill.amount_base:.6f} @ {fill.price:.4f} "
                      f"= {fill.value_quote:.2f} (frais {fill.fee_quote:.3f}) "
                      f"PnL {pnl:+.2f} [{reason}]")
        if reason in ("stop_loss", "take_profit") and self.alert_forced:
            label = "Stop de perte" if reason == "stop_loss" else "Objectif atteint"
            self._alert(f"{label} {p.symbol}", f"Sortie a {fill.price:.4f}, PnL {pnl:+.2f} {self.quote}.",
                        "high" if reason == "stop_loss" else "default",
                        "chart_with_downwards_trend" if reason == "stop_loss" else "chart_with_upwards_trend")
        return fill

    def _liquidate_all(self, cycle_id: str, prices: dict[str, float]) -> None:
        for p in build_positions(self.storage, self.name, prices):
            did = self.storage.record_decision(
                cycle_id, self.name, p.symbol, "sell", confidence=1.0,
                reasoning="liquidation : coupe-circuit", accepted=True, raw={"forced": "kill_switch"},
            )
            self._execute_sell(cycle_id, p, prices[p.symbol], "kill_switch", did)
        cash = compute_cash(self.storage, self.name, self.capital)
        self.storage.record_equity(self.name, cash, 0.0)

    # ==================================================================
    def reconcile_live(self) -> list[str]:
        """Compare le book reconstruit au compte Binance reel. Retourne la
        liste des ecarts. Une liste vide veut dire : la base dit vrai."""
        balances = self.data.fetch_balances()
        problems: list[str] = []
        for r in self.storage.open_positions(self.name):
            base = r["symbol"].split("/")[0]
            have, need = balances.get(base, 0.0), float(r["amount_base"])
            if have < need * 0.98:
                problems.append(f"{r['symbol']} : la base croit detenir {need:.6f} {base}, "
                                f"le compte n'en a que {have:.6f}")
        cash = compute_cash(self.storage, self.name, self.capital)
        have_q = balances.get(self.quote, 0.0)
        if have_q < cash * 0.95:
            problems.append(f"cash : la base croit avoir {cash:.2f} {self.quote}, "
                            f"le compte n'en a que {have_q:.2f}")
        return problems

    # ==================================================================
    def _summary(self, cycle_id: str, prices: dict[str, float]) -> None:
        t = Table(title=f"Etat apres {cycle_id}", show_lines=False)
        for col in ("book", "cash", "positions", "equity", "perf", "trades", "frais"):
            t.add_column(col, justify="right" if col != "book" else "left")

        cash, pos = self._book(prices)
        pv = sum(p.value_quote for p in pos)
        eq = cash + pv
        perf = (eq / self.capital - 1) * 100
        color = "green" if perf >= 0 else "red"
        t.add_row("claude", f"{cash:.2f}", f"{pv:.2f} ({len(pos)})", f"{eq:.2f}",
                  f"[{color}]{perf:+.2f}%[/]", str(len(self.storage.closed_positions(self.name))),
                  f"{self.storage.total_fees(self.name):.3f}")

        bench = self.benchmark_value(prices)
        if bench is not None:
            bperf = (bench / self.capital - 1) * 100
            bcolor = "green" if bperf >= 0 else "red"
            t.add_row("repere", "0.00", f"{bench:.2f} ({len(self.symbols)})", f"{bench:.2f}",
                      f"[{bcolor}]{bperf:+.2f}%[/]", "-", f"{self.capital * self.fee_rate * 2:.3f}")
        console.print(t)
        if bench is not None:
            gap = perf - (bench / self.capital - 1) * 100
            gcolor = "green" if gap >= 0 else "red"
            console.print(f"ecart claude - repere : [{gcolor}]{gap:+.2f} points[/]")
        api = self.storage.api_cost_total()
        console.print(f"[dim]cout API cumule : {api:.3f} $  |  aujourd'hui : "
                      f"{self.storage.api_cost_today():.3f} $  |  appels : {self.storage.api_calls_total()}[/]")


# ======================================================================
def build_engine(cfg: Config, *, mode_override: str | None = None) -> Engine:
    """Assemble le moteur selon config.yaml. Le live exige LIVE_ARMED."""
    from .config import live_is_armed
    from .brains.llm_brain import LLMBrain

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

    brain = LLMBrain(cfg, storage)
    risk = RiskManager(cfg, storage)
    return Engine(cfg, storage, data, executor, brain, risk)
