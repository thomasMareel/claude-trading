"""Le singe : des traders aleatoires soumis EXACTEMENT aux memes garde-fous
que Claude, sur le meme chemin de prix, de t0 a maintenant.

    python scripts/singe.py [--n 1000] [--seed 1]

Repond a la seule question statistique que ce montage permet : le resultat
de Claude est-il distinguable de ce que le hasard produit sous ces regles ?
Le script rapporte la distribution des singes et le percentile de Claude,
sur le rendement brut et sur l'exces face au jumeau a exposition egale.

Approximation du chien de garde : stop et objectif sont verifies sur le low
et le high de chaque bougie 4h, pas seulement sur la cloture.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import load_config  # noqa: E402
from src.engine import BENCHMARK  # noqa: E402
from src.portfolio import compute_cash  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()
BRAIN = "llm"
TF_MS = 4 * 3600 * 1000
FRICTION_RT = 0.0015


def percentile_of(value: float, sample: np.ndarray) -> float:
    return float((sample < value).mean() * 100)


def load_path(st: Storage, symbols: list[str], tf: str, t0_ms: int, now_ms: int):
    """Bougies cloturees de t0 a maintenant, alignees sur BTC."""
    series = {}
    for s in symbols:
        rows = st._conn.execute(
            "SELECT ts, high, low, close FROM candles WHERE symbol=? AND timeframe=? AND ts>=? AND ts+?<=? ORDER BY ts",
            (s, tf, t0_ms - TF_MS, TF_MS, now_ms),
        ).fetchall()
        series[s] = {int(r["ts"]): (float(r["high"]), float(r["low"]), float(r["close"])) for r in rows}
    common = sorted(set.intersection(*(set(v) for v in series.values())))
    return common, series


def simulate(rng, ts_list, series, symbols, cfg, p_buy, p_sell):
    fee = float(cfg.get("exchange.fee_rate")); slip = float(cfg.get("exchange.slippage"))
    r = cfg.get("risk")
    capital = cfg.total_capital
    maxpos, maxopen = float(r["max_position_pct"]), int(r["max_open_positions"])
    minval = float(r["min_order_value"]); budget = int(r["max_round_trips_per_week"])
    daily = float(r["max_daily_loss_pct"]); kill = float(r["kill_switch_drawdown_pct"])
    sl, tp = float(r["stop_loss_pct"]), float(r["take_profit_pct"])

    cash, positions, trades, opens = capital, {}, 0, 0
    week_key, opens_week = None, 0
    day_key, day_start_eq = None, capital
    expos_after, basket_rets = [], []      # exposition APRES les actions de chaque bougie
    prev_closes = None
    dead = False
    for ts in ts_list:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        wk, dk = dt.strftime("%G-W%V"), dt.strftime("%Y-%m-%d")
        closes = {s: series[s][ts][2] for s in symbols}
        if prev_closes is not None:
            basket_rets.append(np.mean([closes[s] / prev_closes[s] - 1 for s in symbols]))
        eq_before = cash + sum(a * closes[s] for s, (a, *_r) in positions.items())
        if not dead:
            if wk != week_key:
                week_key, opens_week = wk, 0
            if dk != day_key:
                day_key, day_start_eq = dk, eq_before
            # sorties forcees sur low / high (approximation du chien de garde)
            for s in list(positions):
                amount, entry, stop, target = positions[s]
                high, low, _c = series[s][ts]
                px = stop if low <= stop else target if high >= target else None
                if px is not None:
                    v = amount * px * (1 - slip)
                    cash += v - v * fee
                    del positions[s]; trades += 1
            eq = cash + sum(a * closes[s] for s, (a, *_r) in positions.items())
            if eq <= capital * (1 - kill):                   # coupe-circuit : liquidation, fin
                for s, (a, *_r) in positions.items():
                    v = a * closes[s] * (1 - slip); cash += v - v * fee; trades += 1
                positions.clear(); dead = True
            else:
                for s in list(positions):                    # ventes aleatoires
                    if rng.random() < p_sell:
                        a = positions.pop(s)[0]
                        v = a * closes[s] * (1 - slip); cash += v - v * fee; trades += 1
                frozen = eq <= day_start_eq * (1 - daily)    # achats aleatoires sous contraintes
                for s in symbols:
                    if s in positions or frozen or len(positions) >= maxopen or opens_week >= budget:
                        continue
                    if rng.random() >= p_buy:
                        continue
                    size = min(eq * maxpos, cash * 0.995)
                    if size < max(minval, 5.0):
                        continue
                    px = closes[s] * (1 + slip)
                    cash -= size + size * fee
                    positions[s] = (size / px, px, px * (1 - sl), px * (1 + tp))
                    opens_week += 1; opens += 1
        pv = sum(a * closes[s] for s, (a, *_r) in positions.items())
        eq_after = cash + pv
        expos_after.append(0.0 if eq_after <= 0 else pv / eq_after)
        prev_closes = closes
    last = {s: series[s][ts_list[-1]][2] for s in symbols}
    liq = cash + sum(a * last[s] * (1 - slip) * (1 - fee) for s, (a, *_r) in positions.items())
    # jumeau a exposition egale : le rendement de la bougie i vers i+1 est gagne
    # avec l'exposition prise A la bougie i, apres ses actions
    twin = capital
    for i, rb in enumerate(basket_rets):
        e_prev, e_now = expos_after[i], expos_after[i + 1]
        twin *= 1 + e_prev * rb
        twin -= abs(e_now - e_prev) * FRICTION_RT * twin
    return liq / capital - 1, twin / capital - 1, trades, opens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    t0 = st.first_equity_ts(BENCHMARK)
    if not t0:
        console.print("[yellow]Pas de t0 : l'experience n'a pas commence.[/]")
        return 0
    t0_ms = int(datetime.fromisoformat(t0).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ts_list, series = load_path(st, cfg.symbols, cfg.timeframe, t0_ms, now_ms)
    if len(ts_list) < 12:
        console.print(f"[yellow]Fenetre trop courte : {len(ts_list)} bougies cloturees depuis t0.[/]")
        return 0

    # ---- Claude sur la meme fenetre ----
    init = cfg.total_capital
    last = {s: series[s][ts_list[-1]][2] for s in cfg.symbols}
    cash = compute_cash(st, BRAIN, init)
    pos_rows = st.open_positions(BRAIN)
    fee = float(cfg.get("exchange.fee_rate")); slip = float(cfg.get("exchange.slippage"))
    eq = cash + sum(float(p["amount_base"]) * last[p["symbol"]] * (1 - slip) * (1 - fee) for p in pos_rows)
    claude_ret = eq / init - 1
    n_closed = len(st.closed_positions(BRAIN))
    n_opens = st._conn.execute("SELECT COUNT(*) AS n FROM positions WHERE brain=? AND opened_at>=?", (BRAIN, t0)).fetchone()["n"]
    holds = [
        (datetime.fromisoformat(c["closed_at"]) - datetime.fromisoformat(c["opened_at"])).total_seconds() / 14400
        for c in st.closed_positions(BRAIN)
    ]
    median_hold = float(np.median(holds)) if holds else 18.0    # 3 jours par defaut
    # jumeau de Claude
    llm_curve = st.equity_curve(BRAIN); b_curve = st.equity_curve(BENCHMARK)
    twin, prev_b, prev_e = init, None, None
    for br in b_curve:
        cand = [r for r in llm_curve if r["ts"] <= br["ts"]]
        if not cand:
            continue
        lr = cand[-1]
        e = float(lr["positions_value"]) / float(lr["total_quote"]) if float(lr["total_quote"]) > 0 else 0.0
        bv = float(br["total_quote"])
        if prev_b:
            twin *= 1 + prev_e * (bv / prev_b - 1); twin -= abs(e - prev_e) * FRICTION_RT * twin
        prev_b, prev_e = bv, e
    claude_excess = claude_ret - (twin / init - 1)

    # ---- calibrage des singes sur l'activite de Claude ----
    rng = np.random.default_rng(args.seed)
    # detention geometrique : la MEDIANE vaut ln2/p, pas 1/p
    p_sell = float(np.log(2)) / max(median_hold, 1.0)
    target_opens = max(int(n_opens), 1)
    p_buy = target_opens / (len(ts_list) * len(cfg.symbols)) * 2.5
    for _ in range(3):   # cible : autant d'OUVERTURES que Claude (pas de trades clos)
        sample = [simulate(rng, ts_list, series, cfg.symbols, cfg, p_buy, p_sell)[3] for _ in range(150)]
        med = max(float(np.median(sample)), 0.5)
        p_buy = min(max(p_buy * target_opens / med, 1e-4), 0.9)

    console.print(f"[dim]fenetre {t0} -> maintenant : {len(ts_list)} bougies 4h  |  Claude : {n_opens} ouvertures, "
                  f"{n_closed} clos, detention mediane {median_hold:.0f} bougies  |  singes calibres p_buy={p_buy:.4f} p_sell={p_sell:.3f}[/]")
    results = np.array([simulate(rng, ts_list, series, cfg.symbols, cfg, p_buy, p_sell) for _ in range(args.n)])
    rets, twins, ntr, nop = results[:, 0], results[:, 1], results[:, 2], results[:, 3]
    excess = rets - twins

    basket_ret = np.mean([last[s] / series[s][ts_list[0]][2] - 1 for s in cfg.symbols])
    regime = "haussier" if basket_ret > 0.10 else "baissier" if basket_ret < -0.10 else "range"
    t = Table(title=f"{args.n} singes sous les memes regles  |  panier {basket_ret*100:+.1f} % ({regime})", pad_edge=False)
    t.add_column("mesure");
    for p in ("p5", "p20", "p50", "p80", "p95"):
        t.add_column(p, justify="right")
    t.add_column("Claude", justify="right"); t.add_column("percentile", justify="right")
    for label, arr, cv in (("rendement brut", rets, claude_ret), ("exces vs jumeau B2", excess, claude_excess)):
        q = np.percentile(arr, [5, 20, 50, 80, 95])
        pc = percentile_of(cv, arr)
        color = "green" if pc >= 95 else "yellow" if pc >= 80 else "red" if pc <= 5 else "white"
        t.add_row(label, *[f"{v*100:+.1f} %" for v in q], f"{cv*100:+.1f} %", f"[{color}]p{pc:.0f}[/]")
    q = np.percentile(ntr, [5, 20, 50, 80, 95])
    t.add_row("trades clos", *[f"{v:.0f}" for v in q], f"{n_closed}", "-")
    q = np.percentile(nop, [5, 20, 50, 80, 95])
    t.add_row("ouvertures", *[f"{v:.0f}" for v in q], f"{n_opens}", "-")
    console.print(t)
    console.print("[dim]Lecture (docs/protocole.md) : au-dessus de p95 = signal notable, entre p5 et p95 = indistinguable du hasard, "
                  "sous p5 = pire que le hasard. Un singe sur vingt depasse p95 sans aucune competence.[/]")
    if n_closed < 20:
        console.print(f"[yellow]{n_closed} trades clos : sous le minimum de 20 du protocole, aucun verdict financier.[/]")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
