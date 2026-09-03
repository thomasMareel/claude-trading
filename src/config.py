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

    # --- raccourcis les plus utilises ---
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

    def brain_capital(self, brain: str) -> float:
        alloc = float(self.get(f"experiment.allocation.{brain}", 0.0))
        return self.total_capital * alloc


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = Config(raw=raw)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Echoue tot et bruyamment plutot que de trader avec une config absurde."""
    alloc = cfg.get("experiment.allocation", {}) or {}
    total = sum(float(v) for v in alloc.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            f"experiment.allocation doit sommer a 1.0, trouve {total:.4f}"
        )
    if cfg.total_capital <= 0:
        raise ValueError("experiment.total_capital doit etre > 0")
    if not cfg.symbols:
        raise ValueError("exchange.symbols est vide")
    if cfg.mode not in ("paper", "live"):
        raise ValueError(f"engine.mode invalide : {cfg.mode!r} (paper ou live)")
    if cfg.get("risk.allow_leverage") or cfg.get("risk.allow_short"):
        raise ValueError(
            "Ce banc d'essai est concu pour du spot long-only. "
            "Reactiver le levier ou le short demande de reecrire la couche de risque."
        )
    mp = float(cfg.get("risk.max_position_pct", 0))
    if not 0 < mp <= 1:
        raise ValueError("risk.max_position_pct doit etre dans ]0, 1]")


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
