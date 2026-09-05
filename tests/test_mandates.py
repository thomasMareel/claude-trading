"""Les mandats : chargement, bornes, application a la configuration, prompt."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import yaml  # noqa: E402

from src import mandates  # noqa: E402

GOOD = {
    "id": "test", "nom": "Test", "famille": "F", "accroche": "a", "philosophie": "p",
    "ce_que_claude_regarde": ["x"], "brief": "b" * 300, "univers": ["BTC/USDT"],
    "horizon_bougies": "6", "ouvertures_par_semaine_attendues": "1",
    "risque": {"max_position_pct": 0.3, "stop_loss_pct": 0.06, "take_profit_pct": 0.12, "max_round_trips_per_week": 2},
    "quand_ca_marche": "m", "quand_ca_casse": "c", "pour_qui": ["prudent"], "axes": {"style": "s"},
}


def write(dir_: Path, mid: str, **over):
    data = {**GOOD, "id": mid, **over}
    (dir_ / f"{mid}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def with_libre(tmp_path: Path) -> Path:
    write(tmp_path, "libre", univers=list(mandates.ALLOWED_UNIVERSE),
          risque={"max_position_pct": 0.4, "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "max_round_trips_per_week": 4})
    return tmp_path


def test_shipped_catalog_loads_and_has_the_control_mandate():
    all_m = mandates.load_all()
    assert "libre" in all_m
    for m in all_m.values():
        for k, (lo, hi) in mandates.BOUNDS.items():
            assert lo <= m.risque[k] <= hi, (m.id, k)


def test_risk_out_of_bounds_is_refused(tmp_path):
    d = with_libre(tmp_path)
    write(d, "fou", risque={**GOOD["risque"], "stop_loss_pct": 0.30})
    with pytest.raises(mandates.MandateError, match="stop_loss_pct"):
        mandates.load_all(d)


def test_unknown_risk_key_and_bad_universe_are_refused(tmp_path):
    d = with_libre(tmp_path)
    write(d, "x", risque={**GOOD["risque"], "kill_switch_drawdown_pct": 0.5})
    with pytest.raises(mandates.MandateError, match="ne peut fixer"):
        mandates.load_all(d)
    (d / "x.yaml").unlink()
    write(d, "y", univers=["DOGE/USDT"])
    with pytest.raises(mandates.MandateError, match="univers"):
        mandates.load_all(d)


def test_id_must_match_filename_and_promises_are_forbidden(tmp_path):
    d = with_libre(tmp_path)
    (d / "z.yaml").write_text(yaml.safe_dump({**GOOD, "id": "autre"}), encoding="utf-8")
    with pytest.raises(mandates.MandateError, match="nom du fichier"):
        mandates.load_all(d)
    (d / "z.yaml").unlink()
    write(d, "w", brief="rendement garanti " * 20)
    with pytest.raises(mandates.MandateError, match="promesse"):
        mandates.load_all(d)


def test_missing_control_mandate_is_refused(tmp_path):
    write(tmp_path, "seul")
    with pytest.raises(mandates.MandateError, match="libre"):
        mandates.load_all(tmp_path)


def test_apply_to_config_overrides_risk_and_restricts_universe(tmp_path):
    d = with_libre(tmp_path)
    write(d, "btc", univers=["BTC/USDT"])
    raw = {"experiment": {"mandate": "btc"}, "risk": {"max_position_pct": 0.4, "kill_switch_drawdown_pct": 0.2},
           "exchange": {"symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"]}}
    out = mandates.apply_to_config(raw, d)
    assert out["risk"]["max_position_pct"] == 0.3            # ecrase par le mandat
    assert out["risk"]["stop_loss_pct"] == 0.06
    assert out["risk"]["kill_switch_drawdown_pct"] == 0.2    # hors mandat : inchange
    assert out["exchange"]["symbols"] == ["BTC/USDT"]
    assert out["experiment"]["mandate_nom"] == "Test"


def test_apply_defaults_to_libre_and_rejects_unknown(tmp_path):
    d = with_libre(tmp_path)
    out = mandates.apply_to_config({"exchange": {"symbols": ["BTC/USDT", "ETH/USDT"]}}, d)
    assert out["experiment"]["mandate"] == "libre" and out["exchange"]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    with pytest.raises(mandates.MandateError, match="inconnu"):
        mandates.apply_to_config({"experiment": {"mandate": "nexistepas"}}, d)


def test_prompt_section_carries_the_exact_brief():
    m = mandates.get("libre")
    s = mandates.prompt_section(m)
    assert m.brief.strip() in s and "TON MANDAT" in s and "BTC/USDT" in s
