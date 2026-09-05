"""Le trader Claude, avec un client simule : on verifie le dossier envoye,
le decodage de la reponse, la comptabilite des couts, et les chemins de
repli (refus, JSON casse, plafond de depense)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.brains.base import BrainContext, MarketSnapshot, PositionView  # noqa: E402
from src.brains.llm_brain import OUTPUT_SCHEMA, SYSTEM_PROMPT, LLMBrain, build_packet  # noqa: E402
from src.config import Config  # noqa: E402
from src.storage import Storage  # noqa: E402

CFG = Config(raw={
    "llm": {"model": "claude-opus-5", "effort": "medium", "max_tokens": 8000, "timeout_seconds": 30,
            "max_daily_api_cost_usd": 2.0, "alert_cost_per_call_usd": 0.30,
            "price_input_per_mtok": 5.0, "price_output_per_mtok": 25.0},
    "risk": {"max_position_pct": 0.4},
    "alerts": {"enabled": False},
})


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def fake_response(payload, *, stop="end_turn", thinking="je reflechis", in_tok=3000, out_tok=800):
    content = [
        SimpleNamespace(type="thinking", thinking=thinking),
        SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False)),
    ]
    return SimpleNamespace(
        stop_reason=stop, stop_details=None, content=content,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def _df():
    t0 = 1_700_000_000_000
    rows = [[t0 + i * 14_400_000, 100 + i, 101 + i, 99 + i, 100 + i, 10.0] for i in range(20)]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def _ctx(positions=None):
    df = _df()
    markets = {
        "BTC/USDT": MarketSnapshot("BTC/USDT", 119.0, df, {"rsi": 55.0, "trend": "up"}),
        "ETH/USDT": MarketSnapshot("ETH/USDT", 10.0, df, {"rsi": None, "trend": None}),
    }
    return BrainContext(
        cycle_id="c1", brain="llm", now_iso="2026-09-03T12:00:00+00:00", initial_capital=100.0,
        cash=100.0, positions=positions or [], markets=markets, recent_decisions=[], recent_trades=[],
        round_trips_used=1, round_trips_budget=5, daily_pnl_pct=0.0, fee_rate=0.001,
        limits={"max_position_pct": 0.4},
    )


def make_brain(response):
    st = Storage(":memory:")
    b = LLMBrain(CFG, st)
    b._client = FakeClient(response)
    b._has_credentials = staticmethod(lambda: True)
    return b, st


def test_missing_api_key_becomes_hold(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    st = Storage(":memory:")
    brain = LLMBrain(CFG, st)
    brain._client = FakeClient(fake_response({}))
    decs = brain.decide(_ctx())
    assert all(d.action == "hold" for d in decs)
    assert brain._client.messages.calls == []
    assert any("ANTHROPIC_API_KEY" in e["message"] for e in st.recent_events())


def test_prompt_names_the_single_benchmark_not_a_rules_bot():
    assert "panier equipondere" in SYSTEM_PROMPT
    assert "regles" not in SYSTEM_PROMPT.split("Regles du jeu")[0].lower()


def test_packet_contains_what_the_model_needs():
    p = build_packet(_ctx([PositionView(1, "BTC/USDT", 100.0, 0.4, 40.0, 119.0, "t", 92.0, 115.0)]))
    assert p["book"]["equity"] == 100.0 + 0.4 * 119.0
    assert p["limites"]["round_trips_restants_cette_semaine"] == 4
    assert p["positions_ouvertes"][0]["pnl_pct"] == 19.0
    assert set(p["marches"]) == {"BTC/USDT", "ETH/USDT"}
    assert len(p["marches"]["BTC/USDT"]["dernieres_bougies_4h"]) == 12
    assert p["marches"]["ETH/USDT"]["indicateurs"]["rsi"] is None   # null, pas une valeur inventee


def test_decisions_are_decoded_sized_and_costed():
    resp = fake_response({
        "market_view": "BTC en tendance haussiere, ETH faible.",
        "decisions": [
            {"symbol": "BTC/USDT", "action": "buy", "bias": "up", "size_pct_of_equity": 0.9,
             "confidence": 0.7, "reasoning": "rebond sur EMA50"},
            {"symbol": "ETH/USDT", "action": "hold", "bias": "down", "size_pct_of_equity": 0,
             "confidence": 0.6, "reasoning": "regime baissier"},
        ],
    })
    brain, st = make_brain(resp)
    decs = brain.decide(_ctx())
    by = {d.symbol: d for d in decs}
    assert by["BTC/USDT"].action == "buy"
    assert by["BTC/USDT"].raw["bias"] == "up" and by["ETH/USDT"].raw["bias"] == "down"
    assert "bias" in OUTPUT_SCHEMA["properties"]["decisions"]["items"]["required"]
    assert by["BTC/USDT"].size_quote == 40.0            # 0.9 plafonne a 0.4 x 100
    assert by["BTC/USDT"].raw["size_pct_of_equity"] == 0.4
    assert by["BTC/USDT"].raw["thinking"] == "je reflechis"
    assert by["ETH/USDT"].action == "hold"
    # cout : 3000 x 5 + 800 x 25 = 35000 / 1e6
    assert abs(st.api_cost_today() - 0.035) < 1e-9
    call = brain._client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["schema"] is OUTPUT_SCHEMA
    assert call["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "marches" in call["messages"][0]["content"]


def test_expensive_call_is_flagged_but_not_blocked():
    resp = fake_response({"market_view": "x", "decisions": []}, in_tok=3000, out_tok=20000)
    brain, st = make_brain(resp)
    decs = brain.decide(_ctx())
    assert all(d.action == "hold" for d in decs)         # symboles omis -> hold
    assert st.api_cost_today() > 0.5
    assert any("couteux" in e["message"] for e in st.recent_events())


def test_omitted_symbol_defaults_to_hold():
    resp = fake_response({"market_view": "x", "decisions": [
        {"symbol": "BTC/USDT", "action": "hold", "size_pct_of_equity": 0, "confidence": 0.5, "reasoning": "r"}]})
    brain, _ = make_brain(resp)
    by = {d.symbol: d for d in brain.decide(_ctx())}
    assert by["ETH/USDT"].action == "hold" and "omis" in by["ETH/USDT"].reasoning


def test_refusal_becomes_hold_and_is_logged():
    brain, st = make_brain(fake_response({}, stop="refusal"))
    decs = brain.decide(_ctx())
    assert all(d.action == "hold" for d in decs)
    assert any("refuse" in e["message"] for e in st.recent_events())
    assert st.api_cost_today() > 0  # l'appel a quand meme coute


def test_broken_json_becomes_hold():
    resp = fake_response({})
    resp.content[1].text = "{pas du json"
    brain, st = make_brain(resp)
    assert all(d.action == "hold" for d in brain.decide(_ctx()))
    assert any("JSON invalide" in e["message"] for e in st.recent_events())


def test_daily_runaway_cap_stops_calls():
    brain, st = make_brain(fake_response({}))
    st.record_api_cost("claude-opus-5", 1, 1, 2.0)  # plafond anti-emballement atteint
    decs = brain.decide(_ctx())
    assert all(d.action == "hold" and d.raw.get("skipped") == "budget" for d in decs)
    assert brain._client.messages.calls == []
