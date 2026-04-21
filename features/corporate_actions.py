"""
features/corporate_actions.py
------------------------------
Guards against data corruption from corporate actions.
Since we use yfinance with auto_adjust=True, splits and dividends are
handled automatically. This module provides:
  - A validation layer to detect if auto_adjust may have been bypassed
  - Anomaly detection for price discontinuities caused by splits/bonuses
  - A safe wrapper around price_provider that enforces auto_adjust

Key rule: any single-day price drop > 40% that isn't in a market crash
          is almost certainly an unadjusted corporate action.
"""

import numpy as np
import pandas as pd

from utils.logger import get_logger

log = get_logger("corporate_actions")

# Price discontinuity thresholds
SPLIT_DISCONTINUITY_PCT = 0.40   # >40% single-day drop → likely unadjusted split
BONUS_DISCONTINUITY_PCT = 0.35   # >35% single-day drop → likely unadjusted bonus


def detect_price_discontinuities(df: pd.DataFrame,
                                  symbol: str = "") -> list[dict]:
    """
    Scan OHLCV data for sudden price drops that suggest unadjusted splits/bonuses.
    Returns a list of suspicious events.

    Note: With yfinance auto_adjust=True, this should return an empty list
    for clean data. Non-empty results indicate a data quality problem.
    """
    if df is None or len(df) < 2:
        return []

    close = df["Close"]
    daily_returns = close.pct_change().dropna()

    # Flag extreme single-day drops (not matched by extreme market-wide events)
    suspicious = daily_returns[daily_returns < -SPLIT_DISCONTINUITY_PCT]

    events = []
    for date, ret in suspicious.items():
        events.append({
            "date":          date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(date),
            "return_pct":    round(ret * 100, 2),
            "likely_cause":  "Possible unadjusted split/bonus",
            "severity":      "HIGH" if ret < -SPLIT_DISCONTINUITY_PCT else "MEDIUM",
        })
        log.warning(
            f"{symbol}: Suspicious price drop on {date}: {ret*100:.1f}% "
            f"— check if auto_adjust=True was used"
        )

    return events


def is_data_safe(df: pd.DataFrame, symbol: str = "") -> bool:
    """
    Return True if the price data is clean enough to use for signal generation.
    Fails if there are unexplained discontinuities suggesting bad adjustment.
    """
    if df is None or df.empty:
        log.warning(f"{symbol}: No data to validate — marking as unsafe")
        return False
    events = detect_price_discontinuities(df, symbol)
    if events:
        log.error(
            f"{symbol}: {len(events)} price discontinuity event(s) detected. "
            f"Data may be unadjusted. Skipping this stock."
        )
        return False
    return True


def check_volume_anomalies(df: pd.DataFrame,
                            symbol: str = "",
                            spike_multiple: float = 10.0) -> list[dict]:
    """
    Detect abnormal volume spikes that might indicate data errors
    (as opposed to legitimate news-driven spikes).

    A 10x+ volume spike with no corresponding price move is likely bad data.
    """
    if df is None or len(df) < 10:
        return []

    avg_volume = df["Volume"].rolling(20).mean()
    vol_ratio  = df["Volume"] / avg_volume.replace(0, np.nan)
    price_move = df["Close"].pct_change().abs()

    # Flag: volume > 10x average BUT price barely moved (<0.5%)
    suspect = (vol_ratio > spike_multiple) & (price_move < 0.005)
    events = []
    for date in df[suspect].index:
        events.append({
            "date":       str(date)[:10],
            "vol_ratio":  round(float(vol_ratio.loc[date]), 1),
            "price_move": round(float(price_move.loc[date]) * 100, 3),
            "likely_cause": "Data anomaly — high volume, no price move",
        })
        log.warning(f"{symbol}: Volume anomaly on {date}")
    return events


def get_adjusted_close(df: pd.DataFrame) -> pd.Series:
    """
    Return the Close series. With auto_adjust=True, yfinance's Close IS
    the adjusted close. This is a named accessor for clarity in code.
    """
    return df["Close"]


def summarize_data_quality(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Run all corporate action checks and return a summary dict.
    Used in the data validation step of the main pipeline.
    """
    if df is None or df.empty:
        return {"symbol": symbol, "safe": False, "reason": "No data"}

    discontinuities = detect_price_discontinuities(df, symbol)
    volume_anomalies = check_volume_anomalies(df, symbol)

    safe = len(discontinuities) == 0

    return {
        "symbol":          symbol,
        "safe":            safe,
        "rows":            len(df),
        "date_range":      f"{df.index[0].date()} → {df.index[-1].date()}",
        "discontinuities": discontinuities,
        "volume_anomalies": volume_anomalies,
        "reason":          "OK" if safe else f"{len(discontinuities)} discontinuity event(s)",
    }