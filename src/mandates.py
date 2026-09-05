"""Les mandats : un seul trader, Claude, missionne differemment.

Un mandat est un brief injecte dans le prompt systeme, un profil de risque
dans des bornes fixes, et un univers de paires. Il se choisit AVANT t0 dans
config.yaml (experiment.mandate) et ne change plus pendant la fenetre : le
brief exact est pre-enregistre dans l'evenement protocol_start, et toute
derive est signalee par scripts/metriques.py.

Les fiches vivent dans strategies/<id>.yaml. Elles sont la documentation ET
la configuration : ce que l'utilisateur lit est exactement ce que Claude
recoit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANDATES_DIR = ROOT / "strategies"
DEFAULT = "libre"

# Bornes tranchees par le panel de protocole. Un mandat peut se placer
# n'importe ou dedans, jamais au-dela : la couche de risque reste la loi.
BOUNDS: dict[str, tuple[float, float]] = {
    "max_position_pct": (0.20, 0.40),
    "stop_loss_pct": (0.04, 0.12),
    "take_profit_pct": (0.06, 0.30),
    "max_round_trips_per_week": (1, 4),
}
ALLOWED_UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
REQUIRED = (
    "id", "nom", "famille", "accroche", "philosophie", "ce_que_claude_regarde", "brief", "univers",
    "horizon_bougies", "ouvertures_par_semaine_attendues", "risque", "quand_ca_marche", "quand_ca_casse",
    "pour_qui", "axes",
)


class MandateError(ValueError):
    pass


@dataclass
class Mandate:
    id: str
    nom: str
    famille: str
    accroche: str
    philosophie: str
    ce_que_claude_regarde: list[str]
    brief: str
    univers: list[str]
    horizon_bougies: str
    ouvertures_par_semaine_attendues: str
    risque: dict[str, float]
    quand_ca_marche: str
    quand_ca_casse: str
    pour_qui: list[str]
    axes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate(m: dict[str, Any], path: Path) -> None:
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        raise MandateError(f"{path.name} : champs manquants {missing}")
    if m["id"] != path.stem:
        raise MandateError(f"{path.name} : id {m['id']!r} doit etre egal au nom du fichier {path.stem!r}")
    risque = m["risque"] or {}
    for k, (lo, hi) in BOUNDS.items():
        if k not in risque:
            raise MandateError(f"{path.name} : risque.{k} manquant")
        v = risque[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= float(v) <= hi:
            raise MandateError(f"{path.name} : risque.{k}={v!r} hors bornes [{lo}, {hi}]")
    extra = set(risque) - set(BOUNDS)
    if extra:
        raise MandateError(f"{path.name} : risque ne peut fixer que {sorted(BOUNDS)}, trouve {sorted(extra)}")
    univers = m["univers"] or []
    if not univers or any(s not in ALLOWED_UNIVERSE for s in univers):
        raise MandateError(f"{path.name} : univers doit etre un sous-ensemble non vide de {ALLOWED_UNIVERSE}")
    brief = str(m["brief"]).strip()
    if not 200 <= len(brief) <= 4000:
        raise MandateError(f"{path.name} : brief de {len(brief)} caracteres, attendu entre 200 et 4000")
    for word in ("garanti", "sans risque", "rendement assure"):
        if word in brief.lower():
            raise MandateError(f"{path.name} : le brief contient une promesse interdite ({word!r})")


def load_all(directory: Path = MANDATES_DIR) -> dict[str, Mandate]:
    out: dict[str, Mandate] = {}
    for path in sorted(directory.glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        _validate(raw, path)
        raw["risque"] = {k: (int(v) if k == "max_round_trips_per_week" else float(v)) for k, v in raw["risque"].items()}
        out[raw["id"]] = Mandate(**{k: raw[k] for k in REQUIRED})
    if DEFAULT not in out:
        raise MandateError(f"le mandat temoin {DEFAULT!r} est obligatoire dans {directory}")
    return out


def get(mandate_id: str, directory: Path = MANDATES_DIR) -> Mandate:
    all_ = load_all(directory)
    if mandate_id not in all_:
        raise MandateError(f"mandat {mandate_id!r} inconnu. Disponibles : {sorted(all_)}")
    return all_[mandate_id]


def apply_to_config(raw: dict[str, Any], directory: Path = MANDATES_DIR) -> dict[str, Any]:
    """Applique le mandat choisi a la configuration brute : profil de risque
    et univers. Appele par load_config AVANT la validation, pour que les
    valeurs effectives soient celles qui sont verifiees et pre-enregistrees."""
    exp = raw.setdefault("experiment", {})
    mid = str(exp.get("mandate") or DEFAULT)
    m = get(mid, directory)
    exp["mandate"] = mid
    exp["mandate_nom"] = m.nom
    risk = raw.setdefault("risk", {})
    for k, v in m.risque.items():
        risk[k] = v
    ex = raw.setdefault("exchange", {})
    base = list(ex.get("symbols") or ALLOWED_UNIVERSE)
    ex["symbols"] = [s for s in base if s in m.univers]
    if not ex["symbols"]:
        raise MandateError(f"mandat {mid!r} : aucun symbole commun entre son univers {m.univers} et exchange.symbols {base}")
    return raw


def prompt_section(m: Mandate) -> str:
    """Le bloc ajoute au prompt systeme de base."""
    return (
        f"\n\n=== TON MANDAT : {m.nom} ({m.id}) ===\n"
        f"{m.brief.strip()}\n"
        f"Univers autorise : {', '.join(m.univers)}. Horizon typique d'une position : {m.horizon_bougies}."
    )
