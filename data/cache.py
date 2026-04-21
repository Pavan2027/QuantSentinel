"""
data/cache.py
-------------
SQLite-backed key-value cache with TTL expiry.
Prevents hammering yfinance or NewsAPI on every cycle restart.

Usage:
    cache = Cache()
    cache.set("price:RELIANCE", df.to_json(), ttl_secs=3600)
    raw = cache.get("price:RELIANCE")   # None if expired or missing
"""

import json
from datetime import datetime, timedelta

from utils.db import get_conn, init_db
from utils.logger import get_logger

log = get_logger("cache")


class Cache:
    def __init__(self):
        init_db()   # ensure tables exist

    def set(self, key: str, value: str, ttl_secs: int = 3600):
        """Store a value with a TTL. Value must be a string (use json.dumps if needed)."""
        now = datetime.utcnow()
        expires = now + timedelta(seconds=ttl_secs)
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO cache (key, value, fetched_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value      = excluded.value,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
            """, (key, value, now.isoformat(), expires.isoformat()))
        log.debug(f"Cache SET: {key} (ttl={ttl_secs}s)")

    def get(self, key: str) -> str | None:
        """Retrieve a value. Returns None if missing or expired."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            log.debug(f"Cache MISS: {key}")
            return None
        if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
            log.debug(f"Cache EXPIRED: {key}")
            self.delete(key)
            return None
        log.debug(f"Cache HIT: {key}")
        return row["value"]

    def delete(self, key: str):
        with get_conn() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def clear_expired(self):
        """Housekeeping: remove all expired entries."""
        now = datetime.utcnow().isoformat()
        with get_conn() as conn:
            result = conn.execute(
                "DELETE FROM cache WHERE expires_at < ?", (now,)
            )
        log.info(f"Cache cleaned: {result.rowcount} expired entries removed")

    def flush(self):
        """Wipe the entire cache. Useful for testing."""
        with get_conn() as conn:
            conn.execute("DELETE FROM cache")
        log.info("Cache flushed")

    def set_json(self, key: str, obj, ttl_secs: int = 3600):
        """Convenience: serialize obj to JSON then store."""
        self.set(key, json.dumps(obj, default=str), ttl_secs)

    def get_json(self, key: str):
        """Convenience: retrieve and deserialize JSON. Returns None if miss."""
        raw = self.get(key)
        return json.loads(raw) if raw is not None else None