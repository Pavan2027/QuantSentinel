"""
features/technicals.py
-----------------------
Compute all technical indicators from an OHLCV DataFrame.
Every output signal is normalized to [0.0, 1.0] for use in the scoring function.

Normalization philosophy:
  - 1.0 = bullish signal (encourages BUY)
  - 0.0 = bearish signal (discourages BUY / encourages SELL)
  - 0.5 = neutral

All functions accept a pd.DataFrame with columns [Open, High, Low, Close, Volume]
and return either a float (latest normalized value) or a dict of all signals.
"""

import numpy as np
import pandas as pd

from config.settings import (
    EMA_SHORT,
    EMA_LONG,
    RSI_PERIOD,
    MACD_FAST,
    MACD_SLOW,
    MACD_SIGNAL,
    ATR_PERIOD,
    VOLUME_AVG_PERIOD,
    VOLUME_SPIKE_MULTIPLIER,
    MOMENTUM_PERIOD,
)
from utils.logger import get_logger

log = get_logger("technicals")


# =============================================================================
# INDIVIDUAL INDICATORS
# =============================================================================

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = None) -> pd.Series:
    """
    Relative Strength Index (0–100).
    Standard Wilder smoothing method.
    """
    if period is None:
        period = RSI_PERIOD
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)   # fill NaN with neutral


def compute_macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD line, Signal line, Histogram.
    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast   = compute_ema(series, MACD_FAST)
    ema_slow   = compute_ema(series, MACD_SLOW)
    macd_line  = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, MACD_SIGNAL)
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_atr(df: pd.DataFrame, period: int = None) -> pd.Series:
    """
    Average True Range — measures volatility.
    Lower ATR relative to price = calmer stock.
    """
    if period is None:
        period = ATR_PERIOD
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_momentum(series: pd.Series, period: int = None) -> pd.Series:
    """
    Rate of Change (ROC) momentum:  (price_now - price_N_ago) / price_N_ago
    Positive = upward momentum, Negative = downward.
    """
    if period is None:
        period = MOMENTUM_PERIOD
    return series.pct_change(periods=period).fillna(0)


def compute_volume_ratio(df: pd.DataFrame) -> pd.Series:
    """
    Current volume as a ratio of its N-day moving average.
    > 1.5 = volume spike (potential breakout signal).
    """
    avg_volume = df["Volume"].rolling(window=VOLUME_AVG_PERIOD).mean()
    return (df["Volume"] / avg_volume.replace(0, np.nan)).fillna(1.0)


# =============================================================================
# NORMALIZATION HELPERS
# =============================================================================

def normalize_rsi(rsi_val: float) -> float:
    """
    Normalize RSI to [0, 1] scoring signal.
    RSI 30–50: bullish zone → higher score (oversold recovery)
    RSI 50–70: neutral
    RSI >70:   overbought → penalize
    RSI <30:   very oversold → still penalize (catching falling knife risk)
    """
    if rsi_val < 20:
        return 0.15   # deeply oversold — risk of further drop
    if rsi_val < 30:
        return 0.40   # oversold — slight bullish lean
    if rsi_val < 50:
        return 0.80   # healthy recovery zone — bullish
    if rsi_val < 65:
        return 0.65   # neutral to slightly bullish (trending stocks sit here)
    if rsi_val < 75:
        return 0.35   # getting overbought — caution
    return 0.10       # overbought — avoid


def normalize_momentum(roc: float) -> float:
    """
    Map rate-of-change to [0, 1].
    ±10% ROC maps to extremes; clipped beyond that.
    """
    clipped = max(-0.10, min(0.10, roc))
    return round((clipped + 0.10) / 0.20, 4)


def normalize_volume(vol_ratio: float) -> float:
    """
    Map volume ratio to [0, 1].
    ratio=1.0 → 0.5 (neutral)
    ratio≥2.0 → 1.0 (high volume spike — bullish confirmation)
    ratio≤0.5 → 0.0 (low volume — weak signal)
    """
    clipped = max(0.5, min(2.0, vol_ratio))
    return round((clipped - 0.5) / 1.5, 4)


def normalize_atr_pct(atr_pct: float) -> float:
    """
    ATR as % of price normalized to [0, 1] where:
    HIGHER score = LOWER volatility (preferred in YELLOW/RED risk states).
    Typical NSE range: 0.5%–5% daily ATR.
    """
    clipped = max(0.005, min(0.05, atr_pct))
    # Invert: low ATR → high score
    return round(1.0 - (clipped - 0.005) / 0.045, 4)


def normalize_macd(histogram: float, price: float) -> float:
    """
    Map MACD histogram (normalized by price) to [0, 1].
    Positive histogram = bullish crossover.
    """
    if price == 0:
        return 0.5
    relative = histogram / price        # make price-independent
    clipped = max(-0.01, min(0.01, relative))
    return round((clipped + 0.01) / 0.02, 4)


# =============================================================================
# MAIN FUNCTION: compute all signals for one stock
# =============================================================================

def compute_all_signals(df: pd.DataFrame, symbol: str = "") -> dict | None:
    """
    Compute all technical signals for a stock given its OHLCV DataFrame.

    Returns:
        dict with keys:
            momentum_score, rsi_score, volume_score, atr_score,
            macd_score, ema_cross_bullish (bool), volume_spike (bool),
            latest_close, ema_short_val, ema_long_val, atr_val, rsi_val
        Returns None if insufficient data.
    """
    min_rows = max(EMA_LONG, MACD_SLOW + MACD_SIGNAL, ATR_PERIOD) + 5
    if df is None or len(df) < min_rows:
        log.warning(f"Insufficient rows for {symbol}: need {min_rows}, got {len(df) if df is not None else 0}")
        return None

    close  = df["Close"]
    latest = float(close.iloc[-1])

    if latest <= 0:
        log.error(f"Invalid close price for {symbol}: {latest}")
        return None

    # --- EMA ---
    ema_short = compute_ema(close, EMA_SHORT)
    ema_long  = compute_ema(close, EMA_LONG)
    ema_short_val = float(ema_short.iloc[-1])
    ema_long_val  = float(ema_long.iloc[-1])
    ema_cross_bullish = ema_short_val > ema_long_val

    # --- RSI ---
    rsi_series = compute_rsi(close)
    rsi_val    = float(rsi_series.iloc[-1])
    rsi_score  = normalize_rsi(rsi_val)

    # --- MACD ---
    macd_line, signal_line, histogram = compute_macd(close)
    hist_val   = float(histogram.iloc[-1])
    macd_score = normalize_macd(hist_val, latest)

    # --- ATR (% of price) ---
    atr_series = compute_atr(df)
    atr_val    = float(atr_series.iloc[-1])
    atr_pct    = atr_val / latest
    atr_score  = normalize_atr_pct(atr_pct)

    # --- Momentum (ROC) ---
    mom_series   = compute_momentum(close)
    mom_val      = float(mom_series.iloc[-1])
    momentum_score = normalize_momentum(mom_val)

    # --- Volume ---
    vol_ratio_series = compute_volume_ratio(df)
    vol_ratio        = float(vol_ratio_series.iloc[-1])
    volume_score     = normalize_volume(vol_ratio)
    volume_spike     = vol_ratio >= VOLUME_SPIKE_MULTIPLIER

    # --- Price vs EMA (bonus signal) ---
    price_above_ema20 = latest > ema_short_val
    price_above_ema50 = latest > ema_long_val

    result = {
        # Normalized scores (all in [0, 1])
        "momentum_score":  momentum_score,
        "rsi_score":       rsi_score,
        "volume_score":    volume_score,
        "atr_score":       atr_score,
        "macd_score":      macd_score,

        # Raw values (for logging and UI)
        "rsi_val":         round(rsi_val, 2),
        "atr_val":         round(atr_val, 4),
        "atr_pct":         round(atr_pct * 100, 3),   # as %
        "momentum_pct":    round(mom_val * 100, 3),   # as %
        "vol_ratio":       round(vol_ratio, 3),
        "macd_histogram":  round(hist_val, 4),
        "latest_close":    round(latest, 2),
        "ema_short_val":   round(ema_short_val, 2),
        "ema_long_val":    round(ema_long_val, 2),

        # Boolean flags
        "ema_cross_bullish":  ema_cross_bullish,
        "price_above_ema20":  price_above_ema20,
        "price_above_ema50":  price_above_ema50,
        "volume_spike":       volume_spike,
    }

    log.debug(
        f"{symbol}: RSI={rsi_val:.1f} ATR%={atr_pct*100:.2f}% "
        f"MOM={mom_val*100:.2f}% VOL_RATIO={vol_ratio:.2f} "
        f"EMA_BULL={ema_cross_bullish}"
    )

    return result