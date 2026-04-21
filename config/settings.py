"""
config/settings.py
------------------
Single source of truth for all constants, thresholds, and environment config.
Never hardcode secrets anywhere else — always read from here.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# =============================================================================
# API KEYS
# =============================================================================
NEWS_API_KEY           = os.getenv("NEWS_API_KEY", "")
UPSTOX_API_KEY         = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET      = os.getenv("UPSTOX_API_SECRET", "")
TWITTER_BEARER_TOKEN   = os.getenv("TWITTER_BEARER_TOKEN", "")
NEWSDATA_API_KEY       = os.getenv("NEWSDATA_API_KEY", "")
MARKETAUX_API_KEY      = os.getenv("MARKETAUX_API_KEY", "")


# =============================================================================
# TELEGRAM BOTS
# =============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


# =============================================================================
# PATHS
# =============================================================================
DB_PATH   = BASE_DIR / "data" / "bot.db"
LOG_DIR   = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# =============================================================================
# PRICE DATA SETTINGS
# =============================================================================
PRICE_LOOKBACK_DAYS     = 90     # days of OHLCV for live cycles (backtester overrides)
CACHE_TTL_PRICE_SECS    = 3600   # 1 hour — don't re-fetch price data within cycle
CACHE_TTL_NEWS_SECS     = 1800   # 30 min — news can refresh more often
YFINANCE_SUFFIX         = ".NS"  # NSE suffix for yfinance (e.g. RELIANCE.NS)


# =============================================================================
# TECHNICAL INDICATOR PARAMETERS
# =============================================================================
EMA_SHORT               = 20
EMA_LONG                = 50
RSI_PERIOD              = 14
MACD_FAST               = 12
MACD_SLOW               = 26
MACD_SIGNAL             = 9
ATR_PERIOD              = 14
VOLUME_AVG_PERIOD       = 20     # days for average volume baseline
VOLUME_SPIKE_MULTIPLIER = 1.5    # volume > 1.5x 20d avg = spike
MOMENTUM_PERIOD         = 10     # days for rate-of-change momentum


# =============================================================================
# NEWS / SENTIMENT SETTINGS
# =============================================================================
NEWS_LOOKBACK_HOURS          = 72    # only fetch news from last 72h
NEWS_STALENESS_CUTOFF_HOURS  = 24    # news older than 24h gets staleness decay
NEWS_MIN_RELEVANCE_SCORE     = 0.4   # minimum relevance to include a headline
MAX_HEADLINES_PER_STOCK      = 10    # cap to avoid FinBERT overload
FINBERT_THRESHOLD            = 0.65  # minimum confidence to trust a prediction
NEWS_REFRESH_HOURS           = int(os.getenv("NEWS_REFRESH_HOURS", 6))

# =============================================================================
# STOCK UNIVERSE FILTERS
# =============================================================================
MIN_STOCK_PRICE_INR     = 100.0      # ₹ minimum price filter
MIN_30D_ADV_CRORE       = 1.0        # ₹1 Crore minimum average daily value
MAX_UNIVERSE_SIZE       = 300        # hard cap on stocks to evaluate per cycle
MAX_SMALLCAP_FRACTION   = 0.3        # max 30% of universe from Smallcap 100


# =============================================================================
# PAPER TRADING SETTINGS
# =============================================================================
PAPER_CAPITAL_INR       = float(os.getenv("PAPER_TRADING_CAPITAL", 100000))
BROKERAGE_PER_ORDER     = 20.0       # ₹20 flat (Upstox/Zerodha standard)
STT_RATE_DELIVERY       = 0.001      # 0.1% STT on delivery sell side
EXCHANGE_TXN_RATE       = 0.0000345  # NSE exchange transaction charge
SLIPPAGE_PCT            = 0.0005     # 0.05% assumed slippage per trade


# =============================================================================
# RISK STATE THRESHOLDS
# =============================================================================
# These gate transitions between GREEN / YELLOW / RED risk states
DRAWDOWN_YELLOW_THRESH  = 0.12   # 12% drawdown triggers YELLOW
DRAWDOWN_RED_THRESH     = 0.20   # 20% drawdown triggers RED
CASH_YELLOW_THRESH      = 0.40   # cash < 40% of starting capital → YELLOW
CASH_RED_THRESH         = 0.20   # cash < 20% of starting capital → RED


# =============================================================================
# SCHEDULER SETTINGS
# =============================================================================
CYCLE_INTERVAL_HOURS    = 0.5
MARKET_OPEN_IST         = "09:30"
MARKET_CLOSE_IST        = "15:00"   # stop new signals 30min before close
MARKET_TIMEZONE         = "Asia/Kolkata"