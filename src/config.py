"""Chargement de la configuration et des secrets.

Regle stricte : les cles API ne transitent que par les variables
d'environnement. Elles ne sont jamais ecrites dans config.yaml, jamais
journalisees, jamais affichees.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LIVE_ARM_FILE = ROOT / "LIVE_ARMED"

EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class Config:
    """Vue objet de config.yaml, avec acces par chemin pointe."""

    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def symbols(self) -> list[str]:
        return list(self.get("exchange.symbols", []))

    @property
    def timeframe(self) -> str:
        return str(self.get("exchange.timeframe", "4h"))

    @property
    def mode(self) -> str:
        return str(self.get("engine.mode", "paper")).lower()

    @property
    def total_capital(self) -> float:
        return float(self.get("experiment.total_capital", 100.0))

    @property
    def mandate(self) -> str:
        return str(self.get("experiment.mandate", "libre"))


class ConfigError(ValueError):
    pass


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    # Le mandat choisi (experiment.mandate) applique son profil de risque et
    # son univers AVANT la validation : ce sont les valeurs effectives qui
    # sont verifiees, puis pre-enregistrees a t0.
    from . import mandates
    try:
        raw = mandates.apply_to_config(raw)
    except mandates.MandateError as e:
        raise ConfigError(str(e)) from e
    cfg = Config(raw=raw)
    validate(cfg)
    return cfg


def _num(cfg: Config, path: str, lo: float, hi: float, *, lo_open: bool = False, hi_open: bool = False) -> float:
    v = cfg.get(path)
    if v is None:
        raise ConfigError(f"{path} manquant")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ConfigError(f"{path} doit etre un nombre, trouve {v!r}")
    lo_ok = x > lo if lo_open else x >= lo
    hi_ok = x < hi if hi_open else x <= hi
    if not (lo_ok and hi_ok):
        left = "]" if lo_open else "["
        right = "[" if hi_open else "]"
        raise ConfigError(f"{path} doit etre dans {left}{lo}, {hi}{right}, trouve {x}")
    return x


def _int(cfg: Config, path: str, lo: int, hi: int) -> int:
    v = cfg.get(path)
    if v is None:
        raise ConfigError(f"{path} manquant")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) != int(v):
        raise ConfigError(f"{path} doit etre un entier, trouve {v!r}")
    x = int(v)
    if not lo <= x <= hi:
        raise ConfigError(f"{path} doit etre dans [{lo}, {hi}], trouve {x}")
    return x


def validate(cfg: Config) -> None:
    """Echoue tot et bruyamment plutot que de trader avec une config absurde.

    Chaque limite de risque est verifiee ici. Une limite declaree mais
    absurde (stop a 0 %, budget a 0) vaudrait une limite absente.
    """
    if cfg.total_capital <= 0:
        raise ConfigError("experiment.total_capital doit etre > 0")
    if not cfg.symbols:
        raise ConfigError("exchange.symbols est vide")
    if len(set(cfg.symbols)) != len(cfg.symbols):
        raise ConfigError("exchange.symbols contient un doublon")
    if cfg.mode not in ("paper", "live"):
        raise ConfigError(f"engine.mode invalide : {cfg.mode!r} (paper ou live)")
    if cfg.get("risk.allow_leverage") or cfg.get("risk.allow_short"):
        raise ConfigError(
            "Ce banc d'essai est concu pour du spot long-only. "
            "Reactiver le levier ou le short demande de reecrire la couche de risque."
        )

    # ---- exchange ----
    fee = _num(cfg, "exchange.fee_rate", 0.0, 0.01)
    slip = _num(cfg, "exchange.slippage", 0.0, 0.01)
    lookback = _int(cfg, "exchange.lookback_candles", 50, 5000)
    ema_slow = _int(cfg, "indicators.ema_slow", 2, 1000)
    if lookback < ema_slow + 20:
        raise ConfigError(
            f"exchange.lookback_candles ({lookback}) doit depasser indicators.ema_slow "
            f"({ema_slow}) d'au moins 20 bougies"
        )

    # ---- risque : chaque borne, puis les interactions ----
    maxpos = _num(cfg, "risk.max_position_pct", 0.0, 1.0, lo_open=True)
    _int(cfg, "risk.max_open_positions", 1, len(cfg.symbols))
    minval = _num(cfg, "risk.min_order_value", 0.0, cfg.total_capital, lo_open=True)
    _int(cfg, "risk.max_round_trips_per_week", 1, 50)
    daily = _num(cfg, "risk.max_daily_loss_pct", 0.0, 1.0, lo_open=True, hi_open=True)
    kill = _num(cfg, "risk.kill_switch_drawdown_pct", 0.0, 1.0, lo_open=True, hi_open=True)
    stop = _num(cfg, "risk.stop_loss_pct", 0.0, 0.5, lo_open=True)
    tp = _num(cfg, "risk.take_profit_pct", 0.0, 5.0, lo_open=True)
    if stop * maxpos >= kill:
        raise ConfigError(
            "un seul stop plein depasserait le coupe-circuit : "
            f"stop {stop:.0%} x position {maxpos:.0%} >= coupe-circuit {kill:.0%}"
        )
    if minval > maxpos * cfg.total_capital * 0.99:
        raise ConfigError(
            f"risk.min_order_value ({minval:.2f}) depasse la taille maximale d'un achat "
            f"({maxpos:.0%} x {cfg.total_capital:.2f} = {maxpos * cfg.total_capital:.2f}) : "
            "le trader ne pourrait jamais acheter"
        )
    if daily >= kill:
        raise ConfigError(
            f"risk.max_daily_loss_pct ({daily:.0%}) doit rester sous le coupe-circuit ({kill:.0%}) : "
            "le gel journalier doit intervenir avant l'arret definitif"
        )
    if tp <= 2 * (fee + slip):
        raise ConfigError(
            f"risk.take_profit_pct ({tp:.2%}) ne couvre pas un aller-retour de frais et slippage "
            f"({2 * (fee + slip):.2%})"
        )

    # ---- llm ----
    if str(cfg.get("llm.effort", "medium")) not in EFFORTS:
        raise ConfigError(f"llm.effort doit etre parmi {EFFORTS}")
    _int(cfg, "llm.max_tokens", 1000, 128_000)
    _num(cfg, "llm.timeout_seconds", 10.0, 600.0)
    _num(cfg, "llm.max_daily_api_cost_usd", 0.0, 100.0, lo_open=True)
    _num(cfg, "llm.alert_cost_per_call_usd", 0.0, 10.0, lo_open=True)

    # ---- moteur ----
    ch = _int(cfg, "engine.cycle_hours", 1, 24)
    if 24 % ch != 0:
        raise ConfigError("engine.cycle_hours doit diviser 24 (1, 2, 3, 4, 6, 8, 12, 24)")
    _int(cfg, "engine.watchdog_minutes", 0, ch * 60)


def secret(name: str) -> str | None:
    """Lit un secret depuis l'environnement. Ne le journalise jamais."""
    val = os.environ.get(name)
    return val.strip() if val else None


def live_is_armed() -> bool:
    """Le mode live exige un fichier LIVE_ARMED cree a la main par l'humain.

    C'est la seconde serrure : meme si engine.mode passe a "live" par
    accident, rien ne part sur le marche sans ce fichier.
    """
    return LIVE_ARM_FILE.exists()
