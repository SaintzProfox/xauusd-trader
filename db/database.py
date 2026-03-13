"""
db/database.py – SQLite persistence layer for signals and trade history.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                action      TEXT    NOT NULL,          -- buy / sell / hold
                volume      REAL    NOT NULL,
                price       REAL    NOT NULL,
                strategy    TEXT,
                indicators  TEXT,                      -- JSON blob
                timestamp   TEXT    NOT NULL,
                notified    INTEGER DEFAULT 0          -- 1 = Telegram sent
            );

            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   INTEGER REFERENCES signals(id),
                symbol      TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                volume      REAL    NOT NULL,
                open_price  REAL,
                close_price REAL,
                pnl         REAL,
                status      TEXT    DEFAULT 'pending', -- pending / open / closed
                opened_at   TEXT,
                closed_at   TEXT,
                notes       TEXT
            );

            CREATE TABLE IF NOT EXISTS price_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                price       REAL    NOT NULL,
                fetched_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_signals_ts  ON signals(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        """)


# ── Signal helpers ─────────────────────────────────────────────────────────────

def save_signal(signal: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO signals (symbol, action, volume, price, strategy,
               indicators, timestamp, notified)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                signal["symbol"],
                signal["action"],
                signal["volume"],
                signal["price"],
                signal.get("strategy"),
                json.dumps(signal.get("indicators", {})),
                signal["timestamp"],
            ),
        )
        return cur.lastrowid


def mark_signal_notified(signal_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE signals SET notified=1 WHERE id=?", (signal_id,))


def get_recent_signals(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_signal_by_id(signal_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM signals WHERE id=?", (signal_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Trade helpers ──────────────────────────────────────────────────────────────

def create_trade(signal_id: int, signal: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO trades (signal_id, symbol, action, volume,
               open_price, status, opened_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                signal_id,
                signal["symbol"],
                signal["action"],
                signal["volume"],
                signal["price"],
                datetime.utcnow().isoformat() + "Z",
            ),
        )
        return cur.lastrowid


def close_trade(trade_id: int, close_price: float, notes: str = ""):
    with get_connection() as conn:
        trade = conn.execute(
            "SELECT * FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not trade:
            return
        direction = 1 if trade["action"] == "buy" else -1
        pnl = direction * (close_price - trade["open_price"]) * trade["volume"] * 100
        conn.execute(
            """UPDATE trades SET close_price=?, pnl=?, status='closed',
               closed_at=?, notes=? WHERE id=?""",
            (close_price, round(pnl, 2),
             datetime.utcnow().isoformat() + "Z", notes, trade_id),
        )


def get_trades(status: str | None = None, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    with get_connection() as conn:
        total     = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        buys      = conn.execute("SELECT COUNT(*) FROM signals WHERE action='buy'").fetchone()[0]
        sells     = conn.execute("SELECT COUNT(*) FROM signals WHERE action='sell'").fetchone()[0]
        closed    = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
        pnl_row   = conn.execute("SELECT SUM(pnl) FROM trades WHERE status='closed'").fetchone()
        total_pnl = round(pnl_row[0] or 0, 2)
        wins      = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0"
        ).fetchone()[0]
        win_rate  = round((wins / closed * 100) if closed > 0 else 0, 1)
        return {
            "total_signals": total,
            "buy_signals":   buys,
            "sell_signals":  sells,
            "closed_trades": closed,
            "total_pnl":     total_pnl,
            "win_rate":      win_rate,
        }


# ── Price cache ────────────────────────────────────────────────────────────────

def cache_price(symbol: str, price: float):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_cache (symbol, price, fetched_at) VALUES (?, ?, ?)",
            (symbol, price, datetime.utcnow().isoformat() + "Z"),
        )


def get_latest_cached_price(symbol: str) -> float | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT price FROM price_cache WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return row[0] if row else None
