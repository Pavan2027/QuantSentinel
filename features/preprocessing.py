"""
features/preprocessing.py
--------------------------
Data cleaning and preprocessing utilities used across the pipeline.
Covers:
  - Headline normalization and SHA deduplication
  - Staleness decay weighting for time-sensitive signals
  - OHLCV DataFrame validation and cleaning
  - Score aggregation across multiple news sources
"""

import hashlib
import re
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from config.settings import (
    NEWS_LOOKBACK_HOURS,
    NEWS_STALENESS_CUTOFF_HOURS,
)
from utils.logger import get_logger

log = get_logger("preprocessing")


# =============================================================================
# HEADLINE DEDUPLICATION
# =============================================================================

def normalize_headline(text: str) -> str:
    """
    Normalize a headline for hashing and comparison.
    - Lowercase
    - Remove punctuation
    - Collapse whitespace
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def headline_hash(text: str) -> str:
    """Return a 16-char SHA-256 hash of a normalized headline."""
    return hashlib.sha256(normalize_headline(text).encode()).hexdigest()[:16]


def deduplicate_headlines(headlines: list[dict]) -> list[dict]:
    """
    Remove duplicate headlines from a mixed list of news records.
    Expects each record to have a "headline" key.
    Deduplication is based on normalized content hash.

    Returns: deduplicated list (preserving order, keeping first occurrence)
    """
    seen = set()
    result = []
    for item in headlines:
        h = headline_hash(item.get("headline", ""))
        if h not in seen:
            seen.add(h)
            result.append({**item, "hash": h})
    removed = len(headlines) - len(result)
    if removed > 0:
        log.debug(f"Dedup: removed {removed} duplicate headlines")
    return result


def merge_news_sources(*source_lists) -> list[dict]:
    """
    Merge multiple lists of news records (from different providers)
    into a single deduplicated, staleness-sorted list.
    """
    combined = []
    for lst in source_lists:
        combined.extend(lst)
    # Sort by staleness (freshest first)
    combined.sort(key=lambda x: x.get("staleness_hrs", 999))
    return deduplicate_headlines(combined)


# =============================================================================
# STALENESS DECAY
# =============================================================================

def staleness_hours(published_at_iso: str) -> float:
    """
    Compute hours since a news article was published.
    Expects ISO 8601 string. Returns float hours.
    """
    if not published_at_iso:
        return float("inf")
    try:
        if published_at_iso.endswith("Z"):
            published_at_iso = published_at_iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(published_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600
    except (ValueError, TypeError):
        return float("inf")


def staleness_decay_weight(hours_old: float) -> float:
    """
    Decay weight in [0.0, 1.0] based on article age.
      0–6h:   1.0  (fully fresh)
      6–24h:  linear decay to 0.0
      >24h:   0.0  (discard)
    """
    cutoff = NEWS_STALENESS_CUTOFF_HOURS
    max_age = NEWS_LOOKBACK_HOURS
    if hours_old <= cutoff:
        return 1.0
    if hours_old >= max_age:
        return 0.0
    return 1.0 - (hours_old - cutoff) / (max_age - cutoff)


def apply_staleness_to_news(news_items: list[dict]) -> list[dict]:
    """
    Add/update staleness_hrs and decay_weight fields in a news list.
    Filters out items with decay_weight == 0.
    """
    result = []
    for item in news_items:
        hours = staleness_hours(item.get("published_at"))
        weight = staleness_decay_weight(hours)
        if weight > 0:
            result.append({
                **item,
                "staleness_hrs": round(hours, 2),
                "decay_weight":  round(weight, 4),
            })
    return result


# =============================================================================
# SENTIMENT SCORE AGGREGATION
# =============================================================================

def aggregate_sentiment_scores(scored_headlines: list[dict]) -> float | None:
    """
    Aggregate multiple per-headline sentiment scores into a single stock score.

    Each item expected:
    {
      "sentiment_score": float,   # 0.0–1.0 (from FinBERT)
      "decay_weight":    float,   # 0.0–1.0 (from staleness)
    }

    Returns:
      Weighted average sentiment score (0.0–1.0), or None if no valid inputs.

    Formula:
      score = Σ(sentiment_i × decay_i) / Σ(decay_i)
    """
    if not scored_headlines:
        return None

    weighted_sum = 0.0
    total_weight = 0.0

    for item in scored_headlines:
        s = item.get("sentiment_score")
        w = item.get("decay_weight", 1.0)
        if s is None or w == 0:
            continue
        weighted_sum += s * w
        total_weight += w

    if total_weight == 0:
        return None

    return round(weighted_sum / total_weight, 4)


# =============================================================================
# OHLCV DATA CLEANING
# =============================================================================

def clean_ohlcv(df: pd.DataFrame, symbol: str = "") -> pd.DataFrame | None:
    """
    Validate and clean an OHLCV DataFrame before indicator computation.

    Checks:
    - Required columns present
    - No all-NaN rows
    - Positive prices
    - Non-negative volume
    - Monotonic date index
    - Remove obvious data errors (High < Low, etc.)
    """
    required = {"Open", "High", "Low", "Close", "Volume"}
    if df is None or df.empty:
        log.warning(f"Empty DataFrame for {symbol}")
        return None

    missing = required - set(df.columns)
    if missing:
        log.error(f"Missing columns in OHLCV for {symbol}: {missing}")
        return None

    df = df.copy()
    initial_len = len(df)

    # Drop rows where Close is NaN or zero
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]

    # Fix High/Low inversions (data errors)
    bad_hl = df["High"] < df["Low"]
    if bad_hl.any():
        log.warning(f"{symbol}: {bad_hl.sum()} rows have High < Low — fixing by swap")
        df.loc[bad_hl, ["High", "Low"]] = df.loc[bad_hl, ["Low", "High"]].values

    # Non-negative volume
    df["Volume"] = df["Volume"].clip(lower=0)

    # Fill any remaining NaN in OHLCV with forward-fill (max 2 periods)
    df[list(required)] = df[list(required)].ffill(limit=2)
    df = df.dropna(subset=list(required))

    # Sort by date
    df = df.sort_index()

    dropped = initial_len - len(df)
    if dropped > 0:
        log.debug(f"{symbol}: cleaned {dropped}/{initial_len} rows")

    if len(df) < 20:
        log.warning(f"{symbol}: only {len(df)} clean rows — may affect indicator quality")

    return df


def validate_score_dict(scores: dict, symbol: str = "") -> bool:
    """
    Validate that a signal scores dict is complete and values are in [0, 1].
    Returns True if valid, False otherwise.
    """
    required_keys = ["momentum_score", "rsi_score", "volume_score", "atr_score"]
    for key in required_keys:
        if key not in scores:
            log.warning(f"{symbol}: Missing score key '{key}'")
            return False
        val = scores[key]
        if not isinstance(val, (int, float)):
            log.warning(f"{symbol}: Score '{key}' is not numeric: {val}")
            return False
        if not (0.0 <= val <= 1.0):
            log.warning(f"{symbol}: Score '{key}' out of range: {val:.4f}")
            return False
    return True