"""Persistance SQLite : bougies, decisions, ordres, positions, equity.

Le journal est le vrai livrable de l'experience. Chaque decision est
enregistree avec son raisonnement complet, y compris les decisions
refusees par la couche de risque. On doit pouvoir relire l'histoire
entiere sans jamais consulter l'exchange.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    ts         INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id      TEXT    NOT NULL,
    ts            TEXT    NOT NULL,
    brain         TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    size_quote    REAL,
    confidence    REAL,
    reasoning     TEXT,
    accepted      INTEGER NOT NULL,
    reject_reason TEXT,
    raw           TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id     TEXT    NOT NULL,
    ts           TEXT    NOT NULL,
    brain        TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    mode         TEXT    NOT NULL,
    price        REAL    NOT NULL,
    amount_base  REAL    NOT NULL,
    value_quote  REAL    NOT NULL,
    fee_quote    REAL    NOT NULL,
    exchange_id  TEXT,
    decision_id  INTEGER,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS positions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    brain          TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    opened_at      TEXT    NOT NULL,
    closed_at      TEXT,
    entry_price    REAL    NOT NULL,
    exit_price     REAL,
    amount_base    REAL    NOT NULL,
    cost_quote     REAL    NOT NULL,
    proceeds_quote REAL,
    fees_quote     REAL    NOT NULL DEFAULT 0,
    pnl_quote      REAL,
    stop_loss      REAL,
    take_profit    REAL,
    close_reason   TEXT
);

CREATE TABLE IF NOT EXISTS equity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    brain           TEXT NOT NULL,
    cash_quote      REAL NOT NULL,
    positions_value REAL NOT NULL,
    total_quote     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_costs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    day           TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    source  TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_decisions_brain ON decisions(brain, ts);
CREATE INDEX IF NOT EXISTS idx_positions_open  ON positions(brain, status);
CREATE INDEX IF NOT EXISTS idx_orders_brain    ON orders(brain, ts);
CREATE INDEX IF NOT EXISTS idx_equity_brain    ON equity(brain, ts);
CREATE INDEX IF NOT EXISTS idx_costs_day       ON api_costs(day);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utcday() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Storage:
    def __init__(self, db_path: str | Path, journal_path: str | Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path = Path(journal_path) if journal_path else None
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    # ---------------- bougies ----------------
    def upsert_candles(self, symbol: str, timeframe: str, rows: list) -> int:
        if not rows:
            return 0
        with self.tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO candles"
                " (symbol, timeframe, ts, open, high, low, close, volume)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [
                    (symbol, timeframe, int(r[0]), float(r[1]), float(r[2]),
                     float(r[3]), float(r[4]), float(r[5]))
                    for r in rows
                ],
            )
        return len(rows)

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> list:
        cur = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles"
            " WHERE symbol=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
            (symbol, timeframe, limit),
        )
        return list(reversed(cur.fetchall()))

    def candle_count(self, symbol: str, timeframe: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        )
        return int(cur.fetchone()["n"])

    # ---------------- decisions ----------------
    def record_decision(
        self,
        cycle_id: str,
        brain: str,
        symbol: str,
        action: str,
        *,
        size_quote: float | None = None,
        confidence: float | None = None,
        reasoning: str = "",
        accepted: bool = True,
        reject_reason: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> int:
        ts = utcnow_iso()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO decisions"
                " (cycle_id, ts, brain, symbol, action, size_quote, confidence,"
                "  reasoning, accepted, reject_reason, raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cycle_id, ts, brain, symbol, action, size_quote, confidence,
                    reasoning, int(accepted), reject_reason,
                    json.dumps(raw or {}, ensure_ascii=False),
                ),
            )
            decision_id = int(cur.lastrowid)
        if self.journal_path:
            entry = {
                "id": decision_id, "cycle_id": cycle_id, "ts": ts, "brain": brain,
                "symbol": symbol, "action": action, "size_quote": size_quote,
                "confidence": confidence, "accepted": accepted,
                "reject_reason": reject_reason, "reasoning": reasoning,
            }
            with open(self.journal_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return decision_id

    def decisions_for_cycle(self, cycle_id: str) -> list:
        cur = self._conn.execute(
            "SELECT * FROM decisions WHERE cycle_id=? ORDER BY id", (cycle_id,)
        )
        return cur.fetchall()

    def recent_decisions(self, brain: str, limit: int = 10) -> list:
        cur = self._conn.execute(
            "SELECT * FROM decisions WHERE brain=? ORDER BY id DESC LIMIT ?",
            (brain, limit),
        )
        return list(reversed(cur.fetchall()))

    # ---------------- ordres ----------------
    def record_order(
        self, cycle_id: str, brain: str, symbol: str, side: str, mode: str,
        price: float, amount_base: float, value_quote: float, fee_quote: float,
        exchange_id: str | None = None, decision_id: int | None = None,
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO orders (cycle_id, ts, brain, symbol, side, mode, price,"
                " amount_base, value_quote, fee_quote, exchange_id, decision_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, utcnow_iso(), brain, symbol, side, mode, price,
                 amount_base, value_quote, fee_quote, exchange_id, decision_id),
            )
            return int(cur.lastrowid)

    def orders_for(self, brain: str) -> list:
        cur = self._conn.execute(
            "SELECT * FROM orders WHERE brain=? ORDER BY id", (brain,)
        )
        return cur.fetchall()

    def total_fees(self, brain: str | None = None) -> float:
        if brain:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(fee_quote),0) AS f FROM orders WHERE brain=?",
                (brain,),
            )
        else:
            cur = self._conn.execute("SELECT COALESCE(SUM(fee_quote),0) AS f FROM orders")
        return float(cur.fetchone()["f"])

    # ---------------- positions ----------------
    def open_position(
        self, brain: str, symbol: str, entry_price: float, amount_base: float,
        cost_quote: float, fees_quote: float, stop_loss: float, take_profit: float,
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO positions (brain, symbol, status, opened_at, entry_price,"
                " amount_base, cost_quote, fees_quote, stop_loss, take_profit)"
                " VALUES (?,?,'open',?,?,?,?,?,?,?)",
                (brain, symbol, utcnow_iso(), entry_price, amount_base,
                 cost_quote, fees_quote, stop_loss, take_profit),
            )
            return int(cur.lastrowid)

    def close_position(
        self, position_id: int, exit_price: float, proceeds_quote: float,
        fee_quote: float, reason: str,
    ) -> float:
        with self.tx() as c:
            row = c.execute(
                "SELECT cost_quote, fees_quote FROM positions WHERE id=?", (position_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"position {position_id} introuvable")
            total_fees = float(row["fees_quote"]) + fee_quote
            pnl = proceeds_quote - float(row["cost_quote"])
            c.execute(
                "UPDATE positions SET status='closed', closed_at=?, exit_price=?,"
                " proceeds_quote=?, fees_quote=?, pnl_quote=?, close_reason=?"
                " WHERE id=?",
                (utcnow_iso(), exit_price, proceeds_quote, total_fees, pnl,
                 reason, position_id),
            )
        return pnl

    def open_positions(self, brain: str | None = None) -> list:
        if brain:
            cur = self._conn.execute(
                "SELECT * FROM positions WHERE status='open' AND brain=?", (brain,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM positions WHERE status='open'")
        return cur.fetchall()

    def open_position_for(self, brain: str, symbol: str):
        cur = self._conn.execute(
            "SELECT * FROM positions WHERE status='open' AND brain=? AND symbol=?",
            (brain, symbol),
        )
        return cur.fetchone()

    def closed_positions(self, brain: str | None = None) -> list:
        if brain:
            cur = self._conn.execute(
                "SELECT * FROM positions WHERE status='closed' AND brain=?"
                " ORDER BY closed_at", (brain,)
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at"
            )
        return cur.fetchall()

    def round_trips_since(self, brain: str, since_iso: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions"
            " WHERE brain=? AND opened_at >= ?",
            (brain, since_iso),
        )
        return int(cur.fetchone()["n"])

    def realized_pnl_since(self, brain: str, since_iso: str) -> float:
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(pnl_quote), 0) AS p FROM positions"
            " WHERE brain=? AND status='closed' AND closed_at >= ?",
            (brain, since_iso),
        )
        return float(cur.fetchone()["p"])

    # ---------------- equity ----------------
    def record_equity(self, brain: str, cash: float, positions_value: float) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO equity (ts, brain, cash_quote, positions_value, total_quote)"
                " VALUES (?,?,?,?,?)",
                (utcnow_iso(), brain, cash, positions_value, cash + positions_value),
            )

    def equity_curve(self, brain: str) -> list:
        cur = self._conn.execute(
            "SELECT ts, cash_quote, positions_value, total_quote FROM equity"
            " WHERE brain=? ORDER BY ts", (brain,)
        )
        return cur.fetchall()

    def last_equity(self, brain: str):
        cur = self._conn.execute(
            "SELECT * FROM equity WHERE brain=? ORDER BY id DESC LIMIT 1", (brain,)
        )
        return cur.fetchone()

    # ---------------- couts API ----------------
    def record_api_cost(
        self, model: str, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO api_costs (ts, day, model, input_tokens, output_tokens, cost_usd)"
                " VALUES (?,?,?,?,?,?)",
                (utcnow_iso(), utcday(), model, input_tokens, output_tokens, cost_usd),
            )

    def api_cost_today(self) -> float:
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM api_costs WHERE day=?",
            (utcday(),),
        )
        return float(cur.fetchone()["c"])

    def api_cost_total(self) -> float:
        cur = self._conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM api_costs")
        return float(cur.fetchone()["c"])

    # ---------------- evenements ----------------
    def event(
        self, level: str, source: str, message: str, payload: dict[str, Any] | None = None
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO events (ts, level, source, message, payload) VALUES (?,?,?,?,?)",
                (utcnow_iso(), level, source, message,
                 json.dumps(payload or {}, ensure_ascii=False)),
            )

    def recent_events(self, limit: int = 20) -> list:
        cur = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def kill_switch_tripped(self) -> bool:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events"
            " WHERE level='critical' AND source='kill_switch'"
        )
        return int(cur.fetchone()["n"]) > 0
