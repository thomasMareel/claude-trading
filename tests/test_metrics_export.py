"""Metriques partagees et export public : le meme calcul pour le terminal et
le tableau de bord, et aucun secret ne sort."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src import metrics  # noqa: E402
from src.config import Config  # noqa: E402
from src.export import export_all, next_cycle_iso, scrub  # noqa: E402
from src.storage import Storage  # noqa: E402

CFG = Config(raw={
    "experiment": {"total_capital": 100.0, "mandate": "libre"},
    "exchange": {"quote": "USDT", "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "timeframe": "4h",
                 "fee_rate": 0.001, "slippage": 0.0005},
    "risk": {"max_position_pct": 0.4, "max_open_positions": 2, "max_round_trips_per_week": 4,
             "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "max_daily_loss_pct": 0.06,
             "kill_switch_drawdown_pct": 0.2, "min_order_value": 12.0},
    "engine": {"mode": "paper", "cycle_hours": 4},
    "llm": {"model": "claude-opus-5", "effort": "medium"},
    "site": {"repo": "https://github.com/x/y"},
})
T0 = datetime(2026, 9, 10, 0, 1, 30, tzinfo=timezone.utc)   # un cycle de 00:00 UTC
TF = 4 * 3600 * 1000


def seed(st: Storage, *, days: int = 3, btc_drift: float = 0.01):
    """Bougies 4h alignees, un panier a t0, des releves et des decisions avec bias."""
    start = T0.replace(minute=0, second=0) - timedelta(hours=4 * 5)
    n = 6 * days + 8
    for s, base in (("BTC/USDT", 100.0), ("ETH/USDT", 10.0), ("SOL/USDT", 5.0)):
        rows = []
        for i in range(n):
            ts = int((start + timedelta(hours=4 * i)).timestamp() * 1000)
            px = base * (1 + btc_drift) ** i if s == "BTC/USDT" else base
            rows.append([ts, px, px * 1.001, px * 0.999, px, 100.0])
        st.upsert_candles(s, "4h", rows)
    st.record_api_cost("claude-opus-5", 3000, 800, 0.035)
    st.set_benchmark_basket([("BTC/USDT", T0.isoformat(), 100.0, 0.333, 33.33),
                             ("ETH/USDT", T0.isoformat(), 10.0, 3.33, 33.33),
                             ("SOL/USDT", T0.isoformat(), 5.0, 6.66, 33.33)])
    st.event("info", "protocol_start", "t0", {"config": {k: CFG.raw.get(k) for k in ("risk", "exchange")},
                                             "git_commit": "abc123", "mandate": {"id": "libre", "brief": "x"}})
    return start, n


def insert_equity(st, brain, ts: datetime, cash, pv):
    st._conn.execute("INSERT INTO equity (ts, brain, cash_quote, positions_value, total_quote) VALUES (?,?,?,?,?)",
                     (ts.isoformat(timespec="seconds"), brain, cash, pv, cash + pv))
    st._conn.commit()


def insert_decision(st, ts: datetime, cycle_id, symbol, bias, action="hold", accepted=True, reason=None):
    st._conn.execute(
        "INSERT INTO decisions (cycle_id, ts, brain, symbol, action, confidence, reasoning, accepted, reject_reason, raw)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cycle_id, ts.isoformat(timespec="seconds"), "llm", symbol, action, 0.6, "r", int(accepted), reason,
         json.dumps({"bias": bias, "market_view": "vue", "thinking": "t" * 3000})))
    st._conn.commit()


def test_bias_is_scored_on_closed_candles_only_and_by_protocol_cycle():
    st = Storage(":memory:")
    seed(st)
    now = T0 + timedelta(days=3)
    c0 = T0.strftime("%Y%m%dT%H%M%SZ")                       # 00:01 : cycle protocole
    insert_decision(st, T0, c0, "BTC/USDT", "up")             # BTC monte de 1 % par bougie : juste
    insert_decision(st, T0, c0, "ETH/USDT", "up")             # ETH plat : faux (flat)
    insert_decision(st, T0, c0, "SOL/USDT", "flat")           # SOL plat : juste
    t4 = T0 + timedelta(hours=4)
    insert_decision(st, t4, t4.strftime("%Y%m%dT%H%M%SZ"), "BTC/USDT", "down")   # 04:01 : hors protocole, faux
    late = now - timedelta(hours=2)                           # trop recent : en attente
    insert_decision(st, late, late.strftime("%Y%m%dT%H%M%SZ"), "BTC/USDT", "up")
    b = metrics.bias_accuracy(st, CFG, T0.isoformat(), now)
    assert b["protocol"]["scored"] == 3 and b["protocol"]["correct"] == 2
    assert b["all_cycles"]["scored"] == 4 and b["all_cycles"]["correct"] == 2
    assert b["all_cycles"]["pending"] == 1
    assert b["protocol"]["by_symbol"]["BTC/USDT"] == {"correct": 1, "n": 1}


def test_twin_b2_chains_exposure_times_basket_return():
    st = Storage(":memory:")
    seed(st)
    # repere : 100 -> 110 ; Claude expose a 50 % constant sur un book de 100 -> le jumeau fait +5 %
    insert_equity(st, "llm", T0, 50.0, 50.0)
    insert_equity(st, metrics.BENCHMARK, T0, 0.0, 100.0)
    t1 = T0 + timedelta(hours=4)
    insert_equity(st, "llm", t1, 50.0, 50.0)
    insert_equity(st, metrics.BENCHMARK, t1, 0.0, 110.0)
    prices = {"BTC/USDT": 100.0, "ETH/USDT": 10.0, "SOL/USDT": 5.0}
    bench = metrics.benchmarks(st, CFG, prices)
    assert bench["b2_pct"] == pytest.approx(5.0 - 100 * 0.5 * metrics.FRICTION_RT * 100 / 100, abs=0.2)
    assert bench["mean_exposure"] == pytest.approx(0.5)


def test_verdict_status_is_explicitly_not_a_verdict_and_tracks_criteria():
    st = Storage(":memory:")
    seed(st)
    insert_equity(st, "llm", T0, 100.0, 0.0)
    insert_equity(st, metrics.BENCHMARK, T0, 0.0, 100.0)
    prices = {"BTC/USDT": 100.0, "ETH/USDT": 10.0, "SOL/USDT": 5.0}
    m = metrics.compute_all(st, CFG, prices, now=T0 + timedelta(days=2))
    s = m["status"]
    assert s["is_verdict"] is False and s["evaluable_now"] is False
    ids = {c["id"] for c in s["criteria"]}
    assert {"duree", "trades", "stable", "kill", "refus", "gels", "singes"} <= ids
    assert m["window"]["config_drift"] == [] or "mandat (brief)" in m["window"]["config_drift"]


def test_export_writes_every_file_and_scrubs_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECRETSECRETSECRET")
    monkeypatch.setenv("NTFY_TOPIC", "mon-sujet-tres-secret-42")
    st = Storage(":memory:")
    seed(st)
    insert_equity(st, "llm", T0, 100.0, 0.0)
    insert_equity(st, metrics.BENCHMARK, T0, 0.0, 100.0)
    st._conn.execute(
        "INSERT INTO decisions (cycle_id, ts, brain, symbol, action, accepted, reasoning, raw) VALUES (?,?,?,?,?,?,?,?)",
        ("c", T0.isoformat(), "llm", "BTC/USDT", "hold", 1, "r",
         json.dumps({"error": "erreur API 401 avec sk-ant-api03-SECRETSECRETSECRET et mon-sujet-tres-secret-42",
                     "thinking": "y" * 5000, "market_view": "v"})))
    st.event("warning", "llm_brain", "cle refusee : sk-ant-api03-SECRETSECRETSECRET", {"cle": "sk-ant-api03-SECRETSECRETSECRET"})
    st._conn.commit()
    prices = {"BTC/USDT": 100.0, "ETH/USDT": 10.0, "SOL/USDT": 5.0}
    files = export_all(st, CFG, prices, tmp_path, now=T0 + timedelta(hours=1))
    names = {f.name for f in files}
    assert {"etat.json", "courbes.json", "metriques.json", "decisions.json", "trades.json",
            "evenements.json", "mandats.json", "fenetres.json"} <= names
    blob = "".join(f.read_text(encoding="utf-8") for f in files)
    assert "SECRETSECRET" not in blob and "mon-sujet-tres-secret" not in blob
    assert "[secret]" in blob
    json.loads((tmp_path / "etat.json").read_text(encoding="utf-8"))   # JSON valide
    decs = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(decs[0]["thinking"]) <= 2500                             # tronque
    evs = json.loads((tmp_path / "evenements.json").read_text(encoding="utf-8"))
    assert all("payload" not in e for e in evs)                         # liste blanche : pas de payload
    etat = json.loads((tmp_path / "etat.json").read_text(encoding="utf-8"))
    assert etat["liens"]["demande"].endswith("/issues/new?template=demande.yml")
    assert etat["mandat"]["id"] == "libre" and etat["bot"]["vivant"] is True


def test_scrub_masks_binance_like_keys_anywhere():
    key = "A" * 64
    out = scrub({"m": f"cle {key} refusee", "l": [key]}, secrets=[])
    assert key not in json.dumps(out)


def test_next_cycle_lands_on_the_next_boundary_plus_grace():
    now = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
    assert next_cycle_iso(4, now) == "2026-09-10T12:01:30+00:00"
    at_boundary = datetime(2026, 9, 10, 12, 1, 30, tzinfo=timezone.utc)
    assert next_cycle_iso(4, at_boundary) == "2026-09-10T16:01:30+00:00"


def test_remise_a_zero_respecte_les_cles_etrangeres():
    """orders.decision_id pointe vers decisions.id : supprimer les decisions
    avant les ordres leve FOREIGN KEY et laisse la base a moitie effacee."""
    st = Storage(":memory:")
    st._conn.execute("INSERT INTO decisions (cycle_id,ts,brain,symbol,action,accepted) VALUES ('c','t','llm','BTC/EUR','buy',1)")
    st._conn.execute("INSERT INTO orders (cycle_id,ts,brain,symbol,side,mode,price,amount_base,value_quote,fee_quote,decision_id)"
                     " VALUES ('c','t','llm','BTC/EUR','buy','paper',1,1,1,0,1)")
    st._conn.commit()
    counts = st.reset_experiment()
    assert counts["decisions"] == 1 and counts["orders"] == 1
    for t in ("decisions", "orders"):
        assert st._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
