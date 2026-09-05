"""Metriques de l'experience, sous forme de fonctions pures qui rendent des
dictionnaires. Utilisees par scripts/metriques.py (affichage console) et par
src/export.py (tableau de bord). Une seule implementation : ce que l'ecran
montre et ce que le terminal affiche sont le meme calcul.

Rien ici ne rend de verdict. Les seuils et la lecture des cas sont dans
docs/protocole.md ; verdict_status() ne fait que confronter les valeurs
courantes aux criteres ecrits d'avance, et le dit.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .portfolio import build_positions, compute_cash
from .storage import Storage

BRAIN = "llm"
BENCHMARK = "benchmark"
BIAS_BAND = 0.003          # bande neutre de la prevision a 24 h
FRICTION_RT = 0.0015       # frais + slippage d'un aller-retour, pour le jumeau B2
CORR = 0.8                 # correlation supposee entre les trois actifs (n effectif)


def timeframe_ms(tf: str) -> int:
    unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[tf[-1]]
    return int(tf[:-1]) * unit * 1000


def iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def regime_label(basket_ret_pct: float) -> str:
    if basket_ret_pct > 10:
        return "haussier"
    if basket_ret_pct < -10:
        return "baissier"
    return "range"


def close_at(st: Storage, symbol: str, tf: str, ts_ms: int) -> float | None:
    """Close de la derniere bougie CLOTUREE a l'instant donne."""
    row = st._conn.execute(
        "SELECT close FROM candles WHERE symbol=? AND timeframe=? AND ts + ? <= ? ORDER BY ts DESC LIMIT 1",
        (symbol, tf, timeframe_ms(tf), ts_ms),
    ).fetchone()
    return float(row["close"]) if row else None


def _liq(positions, slip: float, fee: float) -> float:
    return sum(p.value_quote for p in positions) * (1 - slip) * (1 - fee)


# ======================================================================
def window(st: Storage, cfg, prices: dict[str, float], now: datetime | None = None) -> dict[str, Any] | None:
    """La fenetre : t0, duree, regime, versions servies, derive de configuration.
    None si t0 n'existe pas encore."""
    t0 = st.first_equity_ts(BENCHMARK)
    if not t0:
        return None
    now = now or datetime.now(timezone.utc)
    fee, slip = float(cfg.get("exchange.fee_rate")), float(cfg.get("exchange.slippage"))
    init = cfg.total_capital
    basket = st.benchmark_basket()
    b1 = sum(float(r["amount_base"]) * prices.get(r["symbol"], float(r["start_price"])) * (1 - slip) for r in basket) * (1 - fee)
    b1_pct = (b1 / init - 1) * 100
    models = Counter(
        r["model"] for r in st._conn.execute("SELECT model FROM api_costs WHERE model != 'timeout-estime'")
    )
    starts = st.events_by_source("protocol_start")
    frozen = json.loads(starts[-1]["payload"] or "{}") if starts else {}
    drift = [k for k, v in (frozen.get("config") or {}).items() if v != cfg.raw.get(k)]
    frozen_mandate = (frozen.get("mandate") or {})
    if frozen_mandate and frozen_mandate.get("brief") is not None:
        from . import mandates
        try:
            current = mandates.get(str(cfg.get("experiment.mandate", mandates.DEFAULT)))
            if current.brief.strip() != str(frozen_mandate.get("brief", "")).strip():
                drift.append("mandat (brief)")
        except Exception:
            drift.append("mandat (introuvable)")
    phase = "reel" if cfg.mode == "live" else "paper"
    target_days = 56 if phase == "reel" else 14
    days = (now - iso(t0)).total_seconds() / 86400
    return {
        "t0": t0,
        "days": round(days, 2),
        "target_days": target_days,
        "phase": phase,
        "regime": regime_label(b1_pct),
        "basket_pct": round(b1_pct, 3),
        "api_calls": st.api_calls_total(),
        "models_served": dict(models),
        "multiple_models": len(models) > 1,
        "config_drift": drift,
        "git_commit": frozen.get("git_commit"),
        "mandate": frozen_mandate.get("id") or str(cfg.get("experiment.mandate", "libre")),
        "kill_switch": st.kill_switch_tripped(),
        "book_uncertain": st.is_flagged("book_uncertain"),
    }


def bilan(st: Storage, cfg, prices: dict[str, float]) -> dict[str, Any]:
    fee, slip = float(cfg.get("exchange.fee_rate")), float(cfg.get("exchange.slippage"))
    init = cfg.total_capital
    cash = compute_cash(st, BRAIN, init)
    pos = build_positions(st, BRAIN, prices)
    pv = _liq(pos, slip, fee)
    eq = cash + pv
    api = st.api_cost_total()
    calls = st.api_calls_total()
    return {
        "capital": init,
        "cash": round(cash, 4),
        "positions_liq": round(pv, 4),
        "equity": round(eq, 4),
        "perf_pct": round((eq / init - 1) * 100, 4),
        "trading_brut": round(eq - init, 4),
        "api_cost": round(api, 4),
        "api_calls": calls,
        "cost_per_call": round(api / calls, 4) if calls else 0.0,
        "net": round(eq - init - api, 4),
        "fees_total": round(st.total_fees(BRAIN), 4),
        "open_positions": [
            {
                "symbol": p.symbol, "entry": p.entry_price, "price": p.current_price,
                "amount": p.amount_base, "value": round(p.value_quote, 4), "pnl_pct": round(p.pnl_pct, 3),
                "opened_at": p.opened_at, "stop": p.stop_loss, "target": p.take_profit,
            }
            for p in pos
        ],
    }


def curves(st: Storage) -> dict[str, list[dict[str, Any]]]:
    claude = [
        {"ts": r["ts"], "total": round(float(r["total_quote"]), 4), "cash": round(float(r["cash_quote"]), 4),
         "positions": round(float(r["positions_value"]), 4)}
        for r in st.equity_curve(BRAIN)
    ]
    bench = [{"ts": r["ts"], "total": round(float(r["total_quote"]), 4)} for r in st.equity_curve(BENCHMARK)]
    return {"claude": claude, "benchmark": bench}


def benchmarks(st: Storage, cfg, prices: dict[str, float]) -> dict[str, Any]:
    """B0 cash, B1 panier, B2 jumeau a exposition egale. B3 (singes) est
    calcule par scripts/singe.py en fin de fenetre, jamais ici."""
    init = cfg.total_capital
    b = bilan(st, cfg, prices)
    llm_curve = st.equity_curve(BRAIN)
    b_curve = st.equity_curve(BENCHMARK)
    twin, prev_b, prev_e = init, None, None
    for br in b_curve:
        cand = [r for r in llm_curve if r["ts"] <= br["ts"]]
        if not cand:
            continue
        lr = cand[-1]
        tot = float(lr["total_quote"])
        e = float(lr["positions_value"]) / tot if tot > 0 else 0.0
        bv = float(br["total_quote"])
        if prev_b is not None and prev_b > 0:
            twin *= 1 + prev_e * (bv / prev_b - 1)
            twin -= abs(e - prev_e) * FRICTION_RT * twin
        prev_b, prev_e = bv, e
    expos = [float(r["positions_value"]) / float(r["total_quote"]) for r in llm_curve if float(r["total_quote"]) > 0]
    mean_expo = sum(expos) / len(expos) if expos else 0.0
    b1_pct = (float(b_curve[-1]["total_quote"]) / init - 1) * 100 if b_curve else None
    if b_curve:
        fee, slip = float(cfg.get("exchange.fee_rate")), float(cfg.get("exchange.slippage"))
        basket = st.benchmark_basket()
        live_b1 = sum(float(r["amount_base"]) * prices.get(r["symbol"], float(r["start_price"])) * (1 - slip) for r in basket) * (1 - fee)
        b1_pct = (live_b1 / init - 1) * 100
    twin_pct = (twin / init - 1) * 100
    perf = b["perf_pct"]
    return {
        "perf_pct": round(perf, 4),
        "b0_pct": 0.0,
        "b1_pct": round(b1_pct, 4) if b1_pct is not None else None,
        "b2_pct": round(twin_pct, 4) if b_curve else None,
        "excess_b0": round(perf, 4),
        "excess_b1": round(perf - b1_pct, 4) if b1_pct is not None else None,
        "excess_b2": round(perf - twin_pct, 4) if b_curve else None,
        "mean_exposure": round(mean_expo, 4),
        "max_drawdown_pct": round(max_drawdown([init] + [float(r["total_quote"]) for r in llm_curve]) * 100, 3),
    }


def max_drawdown(curve: list[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def bias_accuracy(st: Storage, cfg, t0: str, now: datetime | None = None) -> dict[str, Any]:
    """Justesse directionnelle a 24 h. Protocole : une observation par jour
    et par symbole (cycle 00:00 UTC) pour eviter les fenetres qui se
    chevauchent. On rend aussi le score sur TOUS les cycles, indicatif."""
    now = now or datetime.now(timezone.utc)
    tf = cfg.timeframe
    tf_ms = timeframe_ms(tf)
    now_ms = int(now.timestamp() * 1000)
    rows = st._conn.execute(
        "SELECT cycle_id, ts, symbol, raw FROM decisions WHERE brain=? AND ts>=? AND cycle_id NOT LIKE 'WD%' ORDER BY id",
        (BRAIN, t0),
    ).fetchall()
    out = {"protocol": _bias_bucket(), "all_cycles": _bias_bucket()}
    for r in rows:
        raw = json.loads(r["raw"] or "{}")
        bias = raw.get("bias")
        if bias not in ("up", "down", "flat"):
            continue
        t_ms = int(iso(r["ts"]).timestamp() * 1000)
        horizon_ms = t_ms + 24 * 3600 * 1000
        p0 = close_at(st, r["symbol"], tf, t_ms)
        p1 = close_at(st, r["symbol"], tf, horizon_ms) if horizon_ms + tf_ms <= now_ms else None
        buckets = [out["all_cycles"]] + ([out["protocol"]] if r["cycle_id"][9:11] == "00" else [])
        for bk in buckets:
            if p0 is None or p1 is None:
                bk["pending"] += 1
                continue
            ret = p1 / p0 - 1
            truth = "up" if ret > BIAS_BAND else "down" if ret < -BIAS_BAND else "flat"
            ok = bias == truth
            bk["scored"] += 1
            bk["correct"] += int(ok)
            bk["by_symbol"].setdefault(r["symbol"], {"correct": 0, "n": 0})
            bk["by_symbol"][r["symbol"]]["n"] += 1
            bk["by_symbol"][r["symbol"]]["correct"] += int(ok)
            bk["confusion"][f"{bias}->{truth}"] = bk["confusion"].get(f"{bias}->{truth}", 0) + 1
    for bk in out.values():
        if bk["scored"]:
            acc = bk["correct"] / bk["scored"]
            n_eff = bk["scored"] / (1 + 2 * CORR)
            bk["accuracy"] = round(acc, 4)
            bk["n_eff"] = round(n_eff, 1)
            bk["se"] = round((acc * (1 - acc) / max(n_eff, 1)) ** 0.5, 4)
    return out


def _bias_bucket() -> dict[str, Any]:
    return {"scored": 0, "correct": 0, "pending": 0, "accuracy": None, "n_eff": None, "se": None,
            "by_symbol": {}, "confusion": {}}


def process(st: Storage, cfg, t0: str) -> dict[str, Any]:
    tf = cfg.timeframe
    budget = int(cfg.get("risk.max_round_trips_per_week"))
    decs = st._conn.execute(
        "SELECT * FROM decisions WHERE brain=? AND ts>=? AND cycle_id NOT LIKE 'WD%' ORDER BY id", (BRAIN, t0)
    ).fetchall()
    skipped = sum(1 for d in decs if '"skipped": "budget"' in (d["raw"] or ""))
    active = [d for d in decs if d["action"] in ("buy", "sell") and '"forced"' not in (d["raw"] or "")]
    refused = [d for d in active if not d["accepted"]]
    motifs = Counter(
        (d["reject_reason"] or "").split(" : ")[-1].split(" (")[0][:70] for d in refused if d["reject_reason"]
    )
    cycles: dict[str, list[str]] = defaultdict(list)
    for d in decs:
        cycles[d["cycle_id"]].append(d["action"])
    all_hold = sum(1 for acts in cycles.values() if all(a == "hold" for a in acts))
    unusable = sum(1 for d in decs if '"error"' in (d["raw"] or "") and '"skipped"' not in (d["raw"] or ""))

    closed = st.closed_positions(BRAIN)
    reasons = Counter(c["close_reason"] for c in closed)
    cage = reasons.get("stop_loss", 0) + reasons.get("take_profit", 0)
    noise = 0
    for c in closed:
        if c["close_reason"] != "stop_loss":
            continue
        t_ms = int(iso(c["closed_at"]).timestamp() * 1000)
        row = st._conn.execute(
            "SELECT MAX(high) AS h FROM candles WHERE symbol=? AND timeframe=? AND ts>? AND ts<=?",
            (c["symbol"], tf, t_ms, t_ms + 48 * 3600 * 1000),
        ).fetchone()
        if row and row["h"] and float(row["h"]) > float(c["entry_price"]):
            noise += 1
    brier, nb = 0.0, 0
    for c in closed:
        o = st._conn.execute(
            "SELECT d.confidence FROM orders o JOIN decisions d ON d.id=o.decision_id "
            "WHERE o.brain=? AND o.symbol=? AND o.side='buy' AND o.ts<=? ORDER BY o.id DESC LIMIT 1",
            (BRAIN, c["symbol"], c["opened_at"] + "z"),
        ).fetchone()
        if o and o["confidence"] is not None and float(o["confidence"]) < 1.0:
            outcome = 1.0 if float(c["pnl_quote"] or 0) > 0 else 0.0
            brier += (float(o["confidence"]) - outcome) ** 2
            nb += 1
    gross = sum(float(c["pnl_quote"] or 0) + float(c["fees_quote"] or 0) for c in closed)
    fees = sum(float(c["fees_quote"] or 0) for c in closed)
    wins = sum(1 for c in closed if float(c["pnl_quote"] or 0) > 0)
    opens = st._conn.execute(
        "SELECT opened_at FROM positions WHERE brain=? AND opened_at>=?", (BRAIN, t0)
    ).fetchall()
    weeks = Counter(iso(o["opened_at"]).strftime("%G-W%V") for o in opens)
    wdays = Counter(iso(o["opened_at"]).strftime("%a") for o in opens)
    n_weeks = max(len(weeks), 1)
    return {
        "decisions": len(decs),
        "cycles": len(cycles),
        "all_hold_cycles": all_hold,
        "all_hold_share": round(all_hold / max(len(cycles), 1), 4),
        "active": len(active),
        "refused": len(refused),
        "refusal_rate": round(len(refused) / max(len(active), 1), 4),
        "refusal_motifs": dict(motifs.most_common()),
        "skipped_budget": skipped,
        "unusable": unusable,
        "unusable_share": round(unusable / max(len(decs), 1), 4),
        "trades_closed": len(closed),
        "wins": wins,
        "exit_reasons": dict(reasons),
        "cage_share": round(cage / len(closed), 4) if closed else None,
        "noise_stops": noise,
        "stops": reasons.get("stop_loss", 0),
        "brier": round(brier / nb, 4) if nb else None,
        "brier_n": nb,
        "fees_over_gross": round(fees / gross, 4) if gross > 0 else None,
        "opens": len(opens),
        "opens_per_week": dict(sorted(weeks.items())),
        "budget_per_week": budget,
        "budget_use": round(len(opens) / (budget * n_weeks), 4) if opens else 0.0,
        "open_weekdays": dict(wdays),
        "daily_freezes": len(st.events_by_source("daily_freeze")),
    }


def verdict_status(win: dict[str, Any], b: dict[str, Any], bench: dict[str, Any], proc: dict[str, Any]) -> dict[str, Any]:
    """Confronte les valeurs courantes aux criteres du protocole (section 6).
    Ce N'EST PAS un verdict : le verdict se prononce une fois, a la date de
    fin fixee d'avance, avec les singes. Ici : ou en est chaque critere."""
    days_ok = win["days"] >= win["target_days"]
    trades_target = 20 if proc["budget_use"] >= 0.6 else 15
    criteria = [
        {"id": "duree", "label": f"Jours de decision ({win['phase']})", "value": round(win["days"], 1),
         "target": f">= {win['target_days']}", "ok": days_ok, "kind": "evaluable"},
        {"id": "trades", "label": "Trades clos", "value": proc["trades_closed"], "target": f">= {trades_target}",
         "ok": proc["trades_closed"] >= trades_target, "kind": "evaluable"},
        {"id": "stable", "label": "Configuration, prompt et modele inchanges", "value": "oui" if not win["config_drift"] and not win["multiple_models"] else "NON",
         "target": "oui", "ok": not win["config_drift"] and not win["multiple_models"], "kind": "evaluable"},
        {"id": "kill", "label": "Coupe-circuit", "value": "declenche" if win["kill_switch"] else "non",
         "target": "non", "ok": not win["kill_switch"], "kind": "echec"},
        {"id": "refus", "label": "Decisions refusees par le risque", "value": f"{proc['refusal_rate']*100:.0f} %",
         "target": "<= 25 % apres la 2e semaine", "ok": proc["refusal_rate"] <= 0.25 or win["days"] < 14, "kind": "echec"},
        {"id": "gels", "label": "Gels journaliers", "value": proc["daily_freezes"], "target": "< 3",
         "ok": proc["daily_freezes"] < 3, "kind": "echec"},
        {"id": "inexploitable", "label": "Cycles sans decision exploitable", "value": f"{proc['unusable_share']*100:.1f} %",
         "target": "<= 5 %", "ok": proc["unusable_share"] <= 0.05, "kind": "echec"},
        {"id": "dd", "label": "Drawdown maximal", "value": f"{abs(bench['max_drawdown_pct']):.1f} %", "target": "< 15 %",
         "ok": abs(bench["max_drawdown_pct"]) < 15, "kind": "signal"},
        {"id": "brut", "label": "Trading brut", "value": f"{b['trading_brut']:+.2f}", "target": "> 0",
         "ok": b["trading_brut"] > 0, "kind": "signal"},
        {"id": "singes", "label": "Percentile face aux singes (excès vs B2)", "value": "en fin de fenetre",
         "target": "> 95e", "ok": None, "kind": "signal"},
    ]
    evaluable = all(c["ok"] for c in criteria if c["kind"] == "evaluable")
    failed = [c for c in criteria if c["kind"] == "echec" and c["ok"] is False]
    return {
        "is_verdict": False,
        "evaluable_now": evaluable,
        "failed_now": [c["id"] for c in failed],
        "criteria": criteria,
        "note": ("Etat des criteres du protocole a cet instant. Le verdict se prononce une seule fois, "
                 "a la date de fin fixee d'avance, avec la distribution des singes (scripts/singe.py)."),
    }


def compute_all(st: Storage, cfg, prices: dict[str, float], now: datetime | None = None) -> dict[str, Any]:
    """Tout ce que le tableau de bord et le terminal affichent, en un appel."""
    win = window(st, cfg, prices, now)
    out: dict[str, Any] = {"window": win, "bilan": bilan(st, cfg, prices), "curves": curves(st)}
    if win is None:
        out.update({"benchmarks": None, "bias": None, "process": None, "status": None})
        return out
    out["benchmarks"] = benchmarks(st, cfg, prices)
    out["bias"] = bias_accuracy(st, cfg, win["t0"], now)
    out["process"] = process(st, cfg, win["t0"])
    out["status"] = verdict_status(win, out["bilan"], out["benchmarks"], out["process"])
    return out
