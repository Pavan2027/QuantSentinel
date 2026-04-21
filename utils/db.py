"""
utils/db.py
-----------
SQLite schema initialization and query helpers.
All bot state — cache, trades, signals, control flags — lives here.
"""

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime

from config.settings import DB_PATH

# Ensure parent directory exists
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    """Context manager that yields a SQLite connection and auto-commits/closes."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Create all tables if they don't exist.
    Safe to call multiple times (idempotent).
    """
    with get_conn() as conn:
        conn.executescript("""
            -- ---------------------------------------------------------------
            -- Cache: avoid redundant API calls within TTL window
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS cache (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                fetched_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL
            );

            -- ---------------------------------------------------------------
            -- Control flags: UI ↔ Bot communication
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS control_flags (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            -- ---------------------------------------------------------------
            -- Trades: paper + live trade history
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                action          TEXT NOT NULL,   -- BUY / SELL
                qty             INTEGER NOT NULL,
                price           REAL NOT NULL,
                brokerage       REAL DEFAULT 0,
                slippage        REAL DEFAULT 0,
                reason          TEXT,
                risk_state      TEXT,
                pnl             REAL,            -- NULL until SELL
                created_at      TEXT NOT NULL
            );

            -- ---------------------------------------------------------------
            -- Positions: current open positions
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS positions (
                symbol          TEXT PRIMARY KEY,
                qty             INTEGER NOT NULL,
                avg_entry_price REAL NOT NULL,
                stop_loss       REAL NOT NULL,
                take_profit     REAL NOT NULL,
                trailing_stop   REAL NOT NULL,
                entry_date      TEXT NOT NULL,
                risk_state_at_entry TEXT
            );

            -- ---------------------------------------------------------------
            -- Signals: every signal generated (BUY/SELL/HOLD) for audit
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                signal          TEXT NOT NULL,
                score           REAL,
                sentiment_score REAL,
                momentum_score  REAL,
                rsi_score       REAL,
                volume_score    REAL,
                atr_score       REAL,
                risk_state      TEXT,
                created_at      TEXT NOT NULL
            );

            -- ---------------------------------------------------------------
            -- Activity log: human-readable status for UI
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT NOT NULL,
                level       TEXT DEFAULT 'INFO',
                created_at  TEXT NOT NULL
            );

            -- Indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_trades_symbol   ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
        """)
    return True


# =============================================================================
# Control Flags (UI ↔ Bot)
# =============================================================================

def write_control_flag(key: str, value: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO control_flags (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value, datetime.utcnow().isoformat()))


def read_control_flag(key: str, default: str = None) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM control_flags WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


# =============================================================================
# Activity Log (for Streamlit UI)
# =============================================================================

def log_activity(message: str, level: str = "INFO"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (message, level, created_at) VALUES (?, ?, ?)",
            (message, level, datetime.utcnow().isoformat())
        )


def get_recent_activity(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT message, level, created_at
            FROM activity_log
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# Signals
# =============================================================================

def insert_signal(symbol: str, signal: str, score: float,
                  scores: dict, risk_state: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signals
            (symbol, signal, score, sentiment_score, momentum_score,
             rsi_score, volume_score, atr_score, risk_state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, signal, score,
            scores.get("sentiment", 0),
            scores.get("momentum", 0),
            scores.get("rsi", 0),
            scores.get("volume", 0),
            scores.get("atr", 0),
            risk_state,
            datetime.utcnow().isoformat(),
        ))


def get_recent_signals(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM signals ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]