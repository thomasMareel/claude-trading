"""La configuration est validee au demarrage : une limite absurde vaut une
limite absente, et le programme doit refuser de demarrer."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import yaml  # noqa: E402

from src.config import Config, ConfigError, validate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def base() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def check(raw):
    validate(Config(raw=raw))


def test_shipped_config_is_valid():
    check(base())


def test_shipped_config_has_a_single_trader_and_a_benchmark():
    raw = base()
    assert "allocation" not in raw["experiment"]
    assert raw["experiment"]["benchmark"] == "buy_and_hold"
    assert "rules" not in raw


@pytest.mark.parametrize("path,value,fragment", [
    ("risk.stop_loss_pct", 0.0, "stop_loss_pct"),
    ("risk.max_round_trips_per_week", 0, "max_round_trips_per_week"),
    ("risk.max_open_positions", 0, "max_open_positions"),
    ("risk.kill_switch_drawdown_pct", 1.0, "kill_switch_drawdown_pct"),
    ("risk.max_position_pct", 1.5, "max_position_pct"),
    ("exchange.fee_rate", 0.05, "fee_rate"),
    ("llm.effort", "turbo", "effort"),
    ("llm.max_tokens", 200, "max_tokens"),
    ("llm.timeout_seconds", 1, "timeout_seconds"),
    ("engine.cycle_hours", 5, "cycle_hours"),
    ("engine.watchdog_minutes", -1, "watchdog_minutes"),
])
def test_absurd_values_are_refused(path, value, fragment):
    raw = base()
    node = raw
    *parents, leaf = path.split(".")
    for p in parents:
        node = node[p]
    node[leaf] = value
    with pytest.raises(ConfigError, match=fragment):
        check(raw)


@pytest.mark.parametrize("path,value", [
    ("risk.max_open_positions", "abc"),
    ("risk.max_open_positions", None),
    ("risk.max_round_trips_per_week", 2.5),
    ("engine.cycle_hours", "4"),
])
def test_integer_keys_refuse_non_integers_with_a_clear_message(path, value):
    raw = base()
    node = raw
    *parents, leaf = path.split(".")
    for p in parents:
        node = node[p]
    node[leaf] = value
    with pytest.raises(ConfigError, match=leaf):
        check(raw)


def test_min_order_value_cannot_exceed_the_largest_possible_buy():
    raw = base()
    raw["risk"]["min_order_value"] = 50.0          # 40 % de 100 = 40 : aucun achat ne passerait
    with pytest.raises(ConfigError, match="min_order_value"):
        check(raw)


def test_daily_freeze_must_come_before_the_kill_switch():
    raw = base()
    raw["risk"]["max_daily_loss_pct"] = 0.25
    with pytest.raises(ConfigError, match="max_daily_loss_pct"):
        check(raw)


def test_take_profit_must_cover_a_round_trip_of_friction():
    raw = base()
    raw["risk"]["take_profit_pct"] = 0.002
    with pytest.raises(ConfigError, match="take_profit_pct"):
        check(raw)


def test_max_open_positions_cannot_exceed_the_number_of_symbols():
    raw = base()
    raw["risk"]["max_open_positions"] = 4
    with pytest.raises(ConfigError, match="max_open_positions"):
        check(raw)


def test_one_full_stop_must_not_reach_the_kill_switch():
    raw = base()
    raw["risk"]["stop_loss_pct"] = 0.5
    raw["risk"]["max_position_pct"] = 0.5
    raw["risk"]["kill_switch_drawdown_pct"] = 0.2
    with pytest.raises(ConfigError, match="coupe-circuit"):
        check(raw)


def test_lookback_must_cover_slow_ema():
    raw = base()
    raw["exchange"]["lookback_candles"] = 150
    with pytest.raises(ConfigError, match="lookback_candles"):
        check(raw)


def test_leverage_and_short_are_refused():
    for key in ("allow_leverage", "allow_short"):
        raw = base()
        raw["risk"][key] = True
        with pytest.raises(ConfigError, match="spot"):
            check(raw)


def test_missing_risk_param_is_an_error_not_a_silent_default():
    raw = base()
    del raw["risk"]["stop_loss_pct"]
    with pytest.raises(ConfigError, match="manquant"):
        check(raw)
