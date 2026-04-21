"""
utils/logger.py
---------------
Structured JSON logger. Every trade, signal, error, and system event
is logged as a JSON line — easy to parse, easy to query, easy to grep.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytz

from config.settings import LOG_DIR

IST = pytz.timezone("Asia/Kolkata")


class ISTFormatter(logging.Formatter):
    """Format log records with IST timestamp and structured JSON output."""

    def format(self, record: logging.LogRecord) -> str:
        now = datetime.now(IST).isoformat()
        payload = {
            "ts":      now,
            "level":   record.levelname,
            "module":  record.name,
            "message": record.getMessage(),
        }
        # Attach any extra fields passed via logger.info("msg", extra={...})
        for key, val in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            ):
                payload[key] = val
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger that writes:
      - JSON lines to  logs/bot_YYYY-MM-DD.log
      - Human-readable to stdout
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured — avoid duplicate handlers

    logger.setLevel(logging.DEBUG)

    # --- File handler (JSON) ---
    today = datetime.now(IST).strftime("%Y-%m-%d")
    log_file = Path(LOG_DIR) / f"bot_{today}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(ISTFormatter())

    # --- Console handler (readable) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  |  %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


# Convenience loggers for specific domains
def log_trade(action: str, symbol: str, qty: int, price: float,
              reason: str, pnl: float = None):
    """Structured trade event log."""
    logger = get_logger("trades")
    logger.info(
        f"{action} {symbol}",
        extra={
            "event":  "TRADE",
            "action": action,
            "symbol": symbol,
            "qty":    qty,
            "price":  price,
            "reason": reason,
            "pnl":    pnl,
        },
    )


def log_signal(symbol: str, signal: str, score: float,
               risk_state: str, details: dict = None):
    """Structured signal event log."""
    logger = get_logger("signals")
    logger.info(
        f"SIGNAL {signal} → {symbol}  score={score:.3f}",
        extra={
            "event":      "SIGNAL",
            "symbol":     symbol,
            "signal":     signal,
            "score":      score,
            "risk_state": risk_state,
            "details":    details or {},
        },
    )


def log_cycle(cycle_num: int, risk_state: str, universe_size: int,
              signals_generated: int):
    """Log the summary of a full scheduler cycle."""
    logger = get_logger("scheduler")
    logger.info(
        f"Cycle {cycle_num} complete",
        extra={
            "event":             "CYCLE",
            "cycle_num":         cycle_num,
            "risk_state":        risk_state,
            "universe_size":     universe_size,
            "signals_generated": signals_generated,
        },
    )