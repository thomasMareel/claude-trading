"""Export des releves vers le tableau de bord, en JSON statique.

Le depot est PUBLIC. Deux regles absolues :
  1. liste blanche : seuls les champs nommes ici sortent, jamais un
     dictionnaire brut ;
  2. nettoyage : chaque chaine exportee passe par scrub(), qui remplace
     toute valeur de secret presente dans l'environnement et tout motif de
     cle connu. Ceinture et bretelles : aucun secret n'est cense atteindre
     la base, mais un message d'erreur d'API peut en citer un morceau.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import mandates, metrics
from .storage import Storage

SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "BINANCE_API_KEY", "BINANCE_API_SECRET",
              "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET", "NTFY_TOPIC")
KEY_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9]{56,}\b"),          # cles Binance : 64 caracteres alphanumeriques
)
DECISIONS_LIMIT = 400
EVENTS_LIMIT = 200
GRACE_SECONDS = 90


def _secret_values() -> list[str]:
    out = []
    for name in SECRET_ENV:
        v = (os.environ.get(name) or "").strip()
        if len(v) >= 6:
            out.append(v)
    return sorted(out, key=len, reverse=True)


def scrub(value: Any, secrets: list[str] | None = None) -> Any:
    """Nettoie recursivement chaines, listes et dictionnaires."""
    if secrets is None:
        secrets = _secret_values()
    if isinstance(value, str):
        s = value
        for sec in secrets:
            s = s.replace(sec, "[secret]")
        for pat in KEY_PATTERNS:
            s = pat.sub("[secret]", s)
        return s
    if isinstance(value, dict):
        return {str(k): scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets) for v in value]
    return value


def next_cycle_iso(cycle_hours: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    hour = (now.hour // cycle_hours) * cycle_hours
    base = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    nxt = base + timedelta(hours=cycle_hours, seconds=GRACE_SECONDS)
    if nxt <= now:
        nxt += timedelta(hours=cycle_hours)
    return nxt.isoformat(timespec="seconds")


def _write(path: Path, data: Any, secrets: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(scrub(data, secrets), ensure_ascii=False, indent=0, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def _decisions(st: Storage, limit: int) -> list[dict[str, Any]]:
    rows = st._conn.execute(
        "SELECT * FROM decisions WHERE brain=? ORDER BY id DESC LIMIT ?", (metrics.BRAIN, limit)
    ).fetchall()
    out = []
    for r in rows:
        raw = json.loads(r["raw"] or "{}") if r["raw"] else {}
        usage = raw.get("usage") or {}
        out.append({
            "id": int(r["id"]), "cycle_id": r["cycle_id"], "ts": r["ts"], "symbol": r["symbol"],
            "action": r["action"], "size_quote": r["size_quote"], "confidence": r["confidence"],
            "accepted": bool(r["accepted"]), "reject_reason": r["reject_reason"],
            "reasoning": (r["reasoning"] or "")[:3000],
            "market_view": str(raw.get("market_view") or "")[:2000] or None,
            "bias": raw.get("bias"),
            "size_pct": raw.get("size_pct_of_equity"),
            "forced": raw.get("forced"),
            "watchdog": bool(raw.get("watchdog", False)),
            "skipped": raw.get("skipped"),
            "error": (str(raw.get("error"))[:400] if raw.get("error") else None),
            "thinking": (str(raw.get("thinking") or "")[:2500] or None),
            "cost_usd": usage.get("cost_usd"),
        })
    return out


def _trades(st: Storage, prices: dict[str, float]) -> dict[str, Any]:
    closes = []
    for c in st.closed_positions(metrics.BRAIN):
        entry, exit_ = float(c["entry_price"]), float(c["exit_price"] or 0)
        closes.append({
            "id": int(c["id"]), "symbol": c["symbol"], "opened_at": c["opened_at"], "closed_at": c["closed_at"],
            "entry_price": entry, "exit_price": exit_, "amount_base": float(c["amount_base"]),
            "cost_quote": round(float(c["cost_quote"]), 4), "proceeds_quote": round(float(c["proceeds_quote"] or 0), 4),
            "fees_quote": round(float(c["fees_quote"] or 0), 4), "pnl_quote": round(float(c["pnl_quote"] or 0), 4),
            "pnl_pct": round((exit_ / entry - 1) * 100, 3) if entry and exit_ else None,
            "stop_loss": c["stop_loss"], "take_profit": c["take_profit"], "close_reason": c["close_reason"],
        })
    opens = []
    for r in st.open_positions(metrics.BRAIN):
        px = prices.get(r["symbol"])
        entry = float(r["entry_price"])
        opens.append({
            "id": int(r["id"]), "symbol": r["symbol"], "opened_at": r["opened_at"], "entry_price": entry,
            "amount_base": float(r["amount_base"]), "cost_quote": round(float(r["cost_quote"]), 4),
            "price": px, "value_quote": round(float(r["amount_base"]) * px, 4) if px else None,
            "pnl_pct": round((px / entry - 1) * 100, 3) if px and entry else None,
            "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
        })
    return {"ouvertes": opens, "closes": closes}


def _events(st: Storage, limit: int) -> list[dict[str, Any]]:
    return [
        {"id": int(e["id"]), "ts": e["ts"], "level": e["level"], "source": e["source"],
         "message": (e["message"] or "")[:600]}
        for e in st.recent_events(limit)
    ]


def export_all(st: Storage, cfg, prices: dict[str, float], out_dir: Path, *, brain=None,
               now: datetime | None = None) -> list[Path]:
    """Ecrit tous les fichiers du tableau de bord. Retourne les chemins."""
    now = now or datetime.now(timezone.utc)
    secrets = _secret_values()
    out_dir = Path(out_dir)
    written: list[Path] = []

    m = metrics.compute_all(st, cfg, prices, now)
    cycle_hours = int(cfg.get("engine.cycle_hours", 4))
    last_eq = st.last_equity(metrics.BRAIN)
    last_dec = st._conn.execute(
        "SELECT cycle_id, ts FROM decisions WHERE brain=? AND cycle_id NOT LIKE 'WD%' ORDER BY id DESC LIMIT 1",
        (metrics.BRAIN,),
    ).fetchone()
    age_min = None
    if last_eq:
        age_min = round((now - datetime.fromisoformat(last_eq["ts"])).total_seconds() / 60, 1)
    mandate = getattr(brain, "mandate", None)
    if mandate is None:
        try:
            mandate = mandates.get(cfg.mandate)
        except Exception:
            mandate = None

    repo = str(cfg.get("site.repo", "")).rstrip("/")
    etat = {
        "genere_le": now.isoformat(timespec="seconds"),
        "mode": cfg.mode,
        "phase": "reel" if cfg.mode == "live" else "paper",
        "capital": cfg.total_capital,
        "quote": cfg.get("exchange.quote", "USDT"),
        "symbols": cfg.symbols,
        "mandat": ({"id": mandate.id, "nom": mandate.nom, "famille": mandate.famille, "accroche": mandate.accroche}
                   if mandate else {"id": cfg.mandate, "nom": cfg.mandate, "famille": "", "accroche": ""}),
        "dernier_cycle": ({"id": last_dec["cycle_id"], "ts": last_dec["ts"]} if last_dec else None),
        "prochain_cycle_ts": next_cycle_iso(cycle_hours, now),
        "cycle_hours": cycle_hours,
        "bot": {
            "dernier_releve": last_eq["ts"] if last_eq else None,
            "age_min": age_min,
            "vivant": bool(age_min is not None and age_min <= cycle_hours * 60 + 20),
        },
        "drapeaux": {
            "coupe_circuit": st.kill_switch_tripped(),
            "book_incertain": st.is_flagged("book_uncertain"),
        },
        "alertes_configurees": bool((os.environ.get("NTFY_TOPIC") or "").strip()),
        "api": {"cout_total": round(st.api_cost_total(), 4), "cout_jour": round(st.api_cost_today(), 4),
                "appels": st.api_calls_total()},
        "limites": {
            k: cfg.get(f"risk.{k}") for k in (
                "max_position_pct", "max_open_positions", "max_round_trips_per_week", "stop_loss_pct",
                "take_profit_pct", "max_daily_loss_pct", "kill_switch_drawdown_pct", "min_order_value",
            )
        } | {"fee_rate": cfg.get("exchange.fee_rate"), "slippage": cfg.get("exchange.slippage")},
        "modele": {"model": cfg.get("llm.model"), "effort": cfg.get("llm.effort")},
        "liens": {
            "repo": repo or None,
            "demande": f"{repo}/issues/new?template=demande.yml" if repo else None,
            "protocole": f"{repo}/blob/master/docs/protocole.md" if repo else None,
            "cles": f"{repo}/blob/master/docs/cles-api.md" if repo else None,
            "claude_code": "https://claude.ai/code",
        },
        "fenetre": m["window"],
        "bilan": m["bilan"],
        "reperes": m["benchmarks"],
        "statut": m["status"],
    }
    written.append(_write(out_dir / "etat.json", etat, secrets))
    written.append(_write(out_dir / "courbes.json", {"claude": m["curves"]["claude"], "repere": m["curves"]["benchmark"]}, secrets))
    written.append(_write(out_dir / "metriques.json", {"fenetre": m["window"], "bias": m["bias"], "processus": m["process"],
                                                        "reperes": m["benchmarks"], "statut": m["status"]}, secrets))
    written.append(_write(out_dir / "decisions.json", _decisions(st, DECISIONS_LIMIT), secrets))
    written.append(_write(out_dir / "trades.json", _trades(st, prices), secrets))
    written.append(_write(out_dir / "evenements.json", _events(st, EVENTS_LIMIT), secrets))
    try:
        all_m = mandates.load_all()
        written.append(_write(out_dir / "mandats.json",
                              {"actif": cfg.mandate, "mandats": [x.to_dict() for x in all_m.values()]}, secrets))
    except mandates.MandateError as e:
        written.append(_write(out_dir / "mandats.json", {"actif": cfg.mandate, "mandats": [], "erreur": str(e)}, secrets))
    fen = out_dir / "fenetres.json"
    if not fen.exists():
        written.append(_write(fen, [], secrets))
    return written
