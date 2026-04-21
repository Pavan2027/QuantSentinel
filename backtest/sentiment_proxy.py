"""
backtest/sentiment_proxy.py
-----------------------------
Price-based sentiment proxy for historical backtesting.

Since news APIs only provide recent headlines (24-72h), we cannot
retroactively fetch historical news for multi-year backtests.

This module generates plausible sentiment scores from price/volume
action, mimicking the *information content* that real sentiment
would provide:
  - Strong up-move + high volume → bullish sentiment (~0.70-0.85)
  - Strong down-move + high volume → bearish sentiment (~0.15-0.30)
  - Calm markets → neutral sentiment (~0.45-0.55)
  - Gap opens simulate event-driven sentiment (earnings, news)

The proxy adds light randomness (σ=0.05) for realism.

IMPORTANT: This is used ONLY during backtesting. Live trading uses
real FinBERT on real news headlines.
"""

import numpy as np
import pandas as pd
from utils.logger import get_logger

log = get_logger("sentiment_proxy")

# Proxy parameters
MOMENTUM_LOOKBACK = 5       # days for price momentum signal
VOLUME_LOOKBACK   = 20      # days for volume baseline
GAP_THRESHOLD     = 0.02    # 2% gap = "event" day
NOISE_STD         = 0.05    # Gaussian noise for realism


def compute_daily_sentiment(df: pd.DataFrame,
                             target_date,
                             seed: int = None) -> float:
    """
    Compute a sentiment proxy score for a single stock on a given date.

    Uses price data UP TO (not including) target_date to avoid
    lookahead bias — sentiment reflects information available at
    market open, not the day's result.

    Args:
        df:          OHLCV DataFrame with DatetimeIndex
        target_date: The date to compute sentiment for
        seed:        Optional RNG seed for reproducibility

    Returns:
        float in [0, 1]: sentiment proxy score
    """
    # Get data strictly before target_date (no lookahead)
    mask = df.index.date < target_date
    hist = df[mask]

    if len(hist) < VOLUME_LOOKBACK + 5:
        return 0.5  # neutral if insufficient history

    recent = hist.tail(MOMENTUM_LOOKBACK)
    close_prices = recent["Close"].values
    volumes = recent["Volume"].values

    # --- Signal 1: Price Momentum (5-day return vs 20-day avg) ---
    ret_5d = (close_prices[-1] / close_prices[0]) - 1.0
    avg_20d_close = hist.tail(20)["Close"].mean()
    price_vs_avg = (close_prices[-1] / avg_20d_close) - 1.0

    # Normalize: strong move = ±5% maps to ±0.25 from neutral
    momentum_signal = np.clip(ret_5d * 5.0, -0.3, 0.3)  # [-0.3, 0.3]
    trend_signal = np.clip(price_vs_avg * 3.0, -0.15, 0.15)  # [-0.15, 0.15]

    # --- Signal 2: Volume Confirmation ---
    vol_avg = hist.tail(VOLUME_LOOKBACK)["Volume"].mean()
    vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 1.0

    # High volume confirms direction, low volume dampens
    if vol_ratio > 1.5:
        vol_multiplier = 1.3  # high volume amplifies signal
    elif vol_ratio < 0.5:
        vol_multiplier = 0.6  # low volume dampens
    else:
        vol_multiplier = 1.0

    # --- Signal 3: Gap Detection (simulates event-driven sentiment) ---
    gap_signal = 0.0
    if len(hist) >= 2:
        prev_close = hist["Close"].iloc[-2]
        last_open = hist["Open"].iloc[-1] if "Open" in hist.columns else prev_close
        gap_pct = (last_open / prev_close) - 1.0
        if abs(gap_pct) >= GAP_THRESHOLD:
            gap_signal = np.clip(gap_pct * 4.0, -0.2, 0.2)

    # --- Combine signals ---
    raw_sentiment = 0.5 + (momentum_signal + trend_signal + gap_signal) * vol_multiplier

    # --- Add noise for realism ---
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, NOISE_STD)
    raw_sentiment += noise

    # Clamp to [0, 1]
    return float(np.clip(raw_sentiment, 0.0, 1.0))


def compute_universe_sentiment(price_data: dict[str, pd.DataFrame],
                                target_date,
                                base_seed: int = 42) -> dict[str, float]:
    """
    Compute sentiment proxy for all stocks on a given date.

    Args:
        price_data:  {symbol: OHLCV DataFrame}
        target_date: date object
        base_seed:   base seed (each stock gets a unique derived seed)

    Returns:
        {symbol: sentiment_score}
    """
    scores = {}
    for i, (sym, df) in enumerate(price_data.items()):
        # Derive a unique but reproducible seed per stock+date
        date_hash = hash(str(target_date)) & 0xFFFFFFFF
        seed = (base_seed + i * 997 + date_hash) % (2**31)
        scores[sym] = compute_daily_sentiment(df, target_date, seed=seed)

    return scores
