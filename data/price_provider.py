"""
data/price_provider.py
-----------------------
Fetches OHLCV price data for NSE stocks via yfinance.

Key design decisions:
  - auto_adjust=True:  handles splits, bonuses, dividends automatically
  - Caches results in SQLite to avoid re-fetching within TTL
  - Returns a validated, clean DataFrame ready for feature engineering
  - Falls back gracefully and logs failures — never crashes the pipeline
"""

import pandas as pd
import yfinance as yf
import io
from datetime import date, timedelta

from config.settings import (
    PRICE_LOOKBACK_DAYS,
    CACHE_TTL_PRICE_SECS,
    YFINANCE_SUFFIX,
)
from data.cache import Cache
from utils.logger import get_logger

log = get_logger("price_provider")
cache = Cache()

# Required columns after fetch
REQUIRED_COLS = {"Open", "High", "Low", "Close", "Volume"}


def _make_ticker(symbol: str) -> str:
    """Convert bare symbol to yfinance NSE ticker. e.g. RELIANCE → RELIANCE.NS"""
    symbol = symbol.upper().strip()
    if not symbol.endswith(YFINANCE_SUFFIX):
        symbol += YFINANCE_SUFFIX
    return symbol


def get_price_data(symbol: str, lookback_days: int = None) -> pd.DataFrame | None:
    """
    Fetch OHLCV data for a single NSE stock.

    Args:
        symbol:        Stock symbol e.g. 'RELIANCE' or 'RELIANCE.NS'
        lookback_days: Days of history. Defaults to settings.PRICE_LOOKBACK_DAYS

    Returns:
        pd.DataFrame with columns [Open, High, Low, Close, Volume]
        Index: DatetimeIndex (timezone-naive, IST date)
        Returns None on failure.
    """
    if lookback_days is None:
        lookback_days = PRICE_LOOKBACK_DAYS

    ticker = _make_ticker(symbol)
    cache_key = f"price:{ticker}:{lookback_days}d"

    # --- Check cache first ---
    cached = cache.get(cache_key)
    if cached is not None:
        df = pd.read_json(io.StringIO(cached))
        df.index = pd.to_datetime(df.index)
        log.debug(f"Price data served from cache: {ticker}")
        return df

    # --- Fetch from yfinance ---
    end_date   = date.today()
    start_date = end_date - timedelta(days=lookback_days + 10)  # +10 buffer for weekends/holidays

    log.info(f"Fetching price data: {ticker} ({lookback_days}d)")
    try:
        raw = yf.download(
            tickers=ticker,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,       # handles splits, dividends, bonuses
            progress=False,
            threads=False,
        )
    except Exception as e:
        log.error(f"yfinance download failed for {ticker}: {e}")
        return None

    if raw is None or raw.empty:
        log.warning(f"No price data returned for {ticker}")
        return None

    # --- Flatten MultiIndex columns if present ---
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    # --- Validate required columns ---
    missing = REQUIRED_COLS - set(raw.columns)
    if missing:
        log.error(f"Missing columns for {ticker}: {missing}")
        return None

    df = raw[list(REQUIRED_COLS)].copy()

    # --- Clean up ---
    df = df.dropna(how="all")
    df.index = pd.to_datetime(df.index).tz_localize(None)  # timezone-naive
    df = df.sort_index()

    # --- Sanity checks ---
    if len(df) < 20:
        log.warning(f"Insufficient data for {ticker}: only {len(df)} rows")
        return None
    if (df["Close"] <= 0).any():
        log.warning(f"Non-positive close prices detected in {ticker} — check data")
        df = df[df["Close"] > 0]

    # --- Cache result ---
    cache.set(cache_key, df.to_json(), ttl_secs=CACHE_TTL_PRICE_SECS)
    log.info(f"Price data fetched and cached: {ticker} ({len(df)} rows)")

    return df


def get_latest_price(symbol: str) -> float | None:
    """Return the most recent closing price for a symbol."""
    df = get_price_data(symbol, lookback_days=30)
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


def get_price_data_batch(symbols: list[str],
                         lookback_days: int = None) -> dict[str, pd.DataFrame]:
    """
    Fetch price data for multiple symbols.
    Returns dict: {symbol: DataFrame} — failed symbols are omitted.
    """
    results = {}
    for sym in symbols:
        df = get_price_data(sym, lookback_days)
        if df is not None:
            results[sym] = df
        else:
            log.warning(f"Skipping {sym} — no price data available")
    log.info(f"Batch price fetch: {len(results)}/{len(symbols)} succeeded")
    return results


def validate_liquidity(symbol: str,
                        min_price: float = 100.0,
                        min_adv_crore: float = 1.0) -> dict:
    """
    Check if a stock meets minimum liquidity requirements.

    Args:
        symbol:        NSE symbol
        min_price:     Minimum closing price in ₹
        min_adv_crore: Minimum 30-day average daily traded value in ₹ Crore

    Returns:
        dict with keys: passes (bool), last_price, adv_crore, reason
    """
    df = get_price_data(symbol, lookback_days=35)
    if df is None or df.empty:
        return {"passes": False, "reason": "No data available",
                "last_price": None, "adv_crore": None}

    last_price = float(df["Close"].iloc[-1])
    last_30 = df.tail(30)
    # ADV = average(close * volume) over last 30 days, in ₹ Crore
    daily_traded_value = last_30["Close"] * last_30["Volume"]
    adv = float(daily_traded_value.mean())
    adv_crore = adv / 1e7   # convert ₹ to ₹ Crore

    if last_price < min_price:
        return {
            "passes": False,
            "reason": f"Price ₹{last_price:.1f} < minimum ₹{min_price}",
            "last_price": last_price,
            "adv_crore": adv_crore,
        }
    if adv_crore < min_adv_crore:
        return {
            "passes": False,
            "reason": f"30d ADV ₹{adv_crore:.2f}Cr < minimum ₹{min_adv_crore}Cr",
            "last_price": last_price,
            "adv_crore": adv_crore,
        }
    return {
        "passes": True,
        "reason": "OK",
        "last_price": last_price,
        "adv_crore": adv_crore,
    }