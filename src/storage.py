"""Persistance SQLite : bougies, decisions, ordres, positions, equity, repere.

La base est la SEULE source de verite. Le journal JSONL est un miroir
lisible ecrit apres coup, pas un journal de secours.

Invariant : un ordre et la position qu'il ouvre ou ferme sont ecrits dans
UNE transaction. Un crash entre les deux ne peut pas laisser de la crypto
detenue mais inconnue de la base.
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

CREATE TABLE IF NOT EXISTS benchmark (
    symbol      TEXT PRIMARY KEY,
    start_ts    TEXT NOT NULL,
    start_price REAL NOT NULL,
    amount_base REAL NOT NULL,
    cost_quote  REAL NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_events_source   ON events(source, level, id);
"""

# Ordre de SUPPRESSION, pas alphabetique : orders porte une cle etrangere vers
# decisions, il doit donc partir en premier. L'ordre inverse leve
# "FOREIGN KEY constraint failed" et laisse la base a moitie effacee.
EXPERIMENT_TABLES = ("orders", "positions", "decisions", "equity", "api_costs", "events", "benchmark")


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
        self, cycle_id: str, brain: str, symbol: str, action: str, *,
        size_quote: float | None = None, confidence: float | None = None,
        reasoning: str = "", accepted: bool = True, reject_reason: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> int:
        ts = utcnow_iso()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO decisions"
                " (cycle_id, ts, brain, symbol, action, size_quote, confidence,"
                "  reasoning, accepted, reject_reason, raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, ts, brain, symbol, action, size_quote, confidence,
                 reasoning, int(accepted), reject_reason,
                 json.dumps(raw or {}, ensure_ascii=False)),
            )
            decision_id = int(cur.lastrowid)
        if self.journal_path:  # miroir lisible, ecrit apres la base
            entry = {
                "id": decision_id, "cycle_id": cycle_id, "ts": ts, "brain": brain,
                "symbol": symbol, "action": action, "size_quote": size_quote,
                "confidence": confidence, "accepted": accepted,
                "reject_reason": reject_reason, "reasoning": reasoning,
            }
            try:
                with open(self.journal_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
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

    # ---------------- remplissages : ordre + position, UNE transaction ----------------
    def record_buy_fill(
        self, *, cycle_id: str, brain: str, symbol: str, mode: str, price: float,
        amount_base: float, value_quote: float, fee_quote: float, exchange_id: str | None,
        decision_id: int | None, stop_loss: float, take_profit: float,
    ) -> tuple[int, int]:
        """Ecrit l'ordre d'achat ET ouvre la position atomiquement.
        Retourne (order_id, position_id)."""
        ts = utcnow_iso()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO orders (cycle_id, ts, brain, symbol, side, mode, price,"
                " amount_base, value_quote, fee_quote, exchange_id, decision_id)"
                " VALUES (?,?,?,?,'buy',?,?,?,?,?,?,?)",
                (cycle_id, ts, brain, symbol, mode, price, amount_base, value_quote,
                 fee_quote, exchange_id, decision_id),
            )
            order_id = int(cur.lastrowid)
            cur = c.execute(
                "INSERT INTO positions (brain, symbol, status, opened_at, entry_price,"
                " amount_base, cost_quote, fees_quote, stop_loss, take_profit)"
                " VALUES (?,?,'open',?,?,?,?,?,?,?)",
                (brain, symbol, ts, price, amount_base, value_quote + fee_quote,
                 fee_quote, stop_loss, take_profit),
            )
            position_id = int(cur.lastrowid)
        return order_id, position_id

    def record_sell_fill(
        self, *, cycle_id: str, brain: str, symbol: str, mode: str, price: float,
        amount_base: float, value_quote: float, fee_quote: float, exchange_id: str | None,
        decision_id: int | None, position_id: int, reason: str,
    ) -> float:
        """Ecrit l'ordre de vente ET ferme la position atomiquement.
        Retourne le PnL net des frais des deux cotes."""
        ts = utcnow_iso()
        with self.tx() as c:
            row = c.execute(
                "SELECT cost_quote, fees_quote, status FROM positions WHERE id=?", (position_id,)
            ).fetchone()
            if row is None or row["status"] != "open":
                raise ValueError(f"position {position_id} introuvable ou deja fermee")
            c.execute(
                "INSERT INTO orders (cycle_id, ts, brain, symbol, side, mode, price,"
                " amount_base, value_quote, fee_quote, exchange_id, decision_id)"
                " VALUES (?,?,?,?,'sell',?,?,?,?,?,?,?)",
                (cycle_id, ts, brain, symbol, mode, price, amount_base, value_quote,
                 fee_quote, exchange_id, decision_id),
            )
            proceeds = value_quote - fee_quote
            pnl = proceeds - float(row["cost_quote"])
            c.execute(
                "UPDATE positions SET status='closed', closed_at=?, exit_price=?,"
                " proceeds_quote=?, fees_quote=?, pnl_quote=?, close_reason=? WHERE id=?",
                (ts, price, proceeds, float(row["fees_quote"]) + fee_quote, pnl, reason, position_id),
            )
        return pnl

    def orders_for(self, brain: str) -> list:
        cur = self._conn.execute("SELECT * FROM orders WHERE brain=? ORDER BY id", (brain,))
        return cur.fetchall()

    def total_fees(self, brain: str | None = None) -> float:
        if brain:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(fee_quote),0) AS f FROM orders WHERE brain=?", (brain,)
            )
        else:
            cur = self._conn.execute("SELECT COALESCE(SUM(fee_quote),0) AS f FROM orders")
        return float(cur.fetchone()["f"])

    # ---------------- positions ----------------
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
                "SELECT * FROM positions WHERE status='closed' AND brain=? ORDER BY closed_at",
                (brain,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at"
            )
        return cur.fetchall()

    def round_trips_since(self, brain: str, since_iso: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE brain=? AND opened_at >= ?",
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
            " WHERE brain=? ORDER BY id", (brain,)
        )
        return cur.fetchall()

    def last_equity(self, brain: str):
        cur = self._conn.execute(
            "SELECT * FROM equity WHERE brain=? ORDER BY id DESC LIMIT 1", (brain,)
        )
        return cur.fetchone()

    def first_equity_ts(self, brain: str) -> str | None:
        row = self._conn.execute(
            "SELECT ts FROM equity WHERE brain=? ORDER BY id LIMIT 1", (brain,)
        ).fetchone()
        return row["ts"] if row else None

    # ---------------- repere buy-and-hold ----------------
    def benchmark_basket(self) -> list:
        return self._conn.execute("SELECT * FROM benchmark ORDER BY symbol").fetchall()

    def set_benchmark_basket(self, rows: list[tuple[str, str, float, float, float]]) -> None:
        """rows : (symbol, start_ts, start_price, amount_base, cost_quote)."""
        with self.tx() as c:
            c.execute("DELETE FROM benchmark")
            c.executemany(
                "INSERT INTO benchmark (symbol, start_ts, start_price, amount_base, cost_quote)"
                " VALUES (?,?,?,?,?)", rows,
            )

    # ---------------- couts API ----------------
    def record_api_cost(self, model: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO api_costs (ts, day, model, input_tokens, output_tokens, cost_usd)"
                " VALUES (?,?,?,?,?,?)",
                (utcnow_iso(), utcday(), model, input_tokens, output_tokens, cost_usd),
            )

    def api_cost_today(self) -> float:
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM api_costs WHERE day=?", (utcday(),)
        )
        return float(cur.fetchone()["c"])

    def api_cost_total(self) -> float:
        cur = self._conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM api_costs")
        return float(cur.fetchone()["c"])

    def api_calls_total(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM api_costs").fetchone()["n"])

    # ---------------- evenements et drapeaux ----------------
    def event(self, level: str, source: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO events (ts, level, source, message, payload) VALUES (?,?,?,?,?)",
                (utcnow_iso(), level, source, message, json.dumps(payload or {}, ensure_ascii=False)),
            )

    def recent_events(self, limit: int = 20) -> list:
        cur = self._conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return cur.fetchall()

    def events_by_source(self, source: str) -> list:
        cur = self._conn.execute("SELECT * FROM events WHERE source=? ORDER BY id", (source,))
        return cur.fetchall()

    def has_event_today(self, source: str) -> bool:
        n = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE source=? AND ts >= ?", (source, utcday())
        ).fetchone()["n"]
        return int(n) > 0

    def kill_switch_tripped(self) -> bool:
        """Le coupe-circuit est definitif : il ne s'acquitte pas."""
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE level='critical' AND source='kill_switch'"
        )
        return int(cur.fetchone()["n"]) > 0

    def is_flagged(self, source: str) -> bool:
        """Vrai s'il existe un evenement critical de cette source posterieur
        au dernier acquittement humain."""
        ack = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS a FROM events"
            " WHERE source=? AND level='info' AND message='acquitte'", (source,)
        ).fetchone()["a"]
        n = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE source=? AND level='critical' AND id > ?",
            (source, int(ack)),
        ).fetchone()["n"]
        return int(n) > 0

    def acknowledge(self, source: str) -> None:
        self.event("info", source, "acquitte")

    # ---------------- remise a zero ----------------
    def reset_experiment(self) -> dict[str, int]:
        """Efface tout sauf les bougies. Retourne ce qui a ete efface."""
        counts = {
            t: int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"])
            for t in EXPERIMENT_TABLES
        }
        with self.tx() as c:
            for t in EXPERIMENT_TABLES:
                c.execute(f"DELETE FROM {t}")
            marks = ",".join("?" * len(EXPERIMENT_TABLES))
            c.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({marks})", EXPERIMENT_TABLES)
        if self.journal_path and self.journal_path.exists():
            try:
                self.journal_path.unlink()
            except OSError:
                pass
        return counts
