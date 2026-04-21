"""
strategy/universe.py
---------------------
Defines the tradeable stock universe for each risk state.

Tiers:
  - NIFTY_100:   100 large-cap stocks (NIFTY 50 + NIFTY Next 50)
  - MIDCAP_100:  100 midcap stocks from NIFTY Midcap 150
  - SMALLCAP_50: 50 curated smallcaps from NIFTY Smallcap 250

Risk-state rules:
  - GREEN:  All 250 stocks
  - YELLOW: NIFTY 100 + top 50 midcaps (150)
  - RED:    NIFTY 100 only (100)

All symbols are bare NSE tickers (no .NS suffix — price_provider adds it).
"""

from config.settings import MAX_UNIVERSE_SIZE
from utils.logger import get_logger

log = get_logger("universe")

# =============================================================================
# NIFTY 50 — Blue-chip, most liquid
# =============================================================================
NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "BAJFINANCE", "LT", "HCLTECH", "ASIANPAINT", "AXISBANK",
    "MARUTI", "SUNPHARMA", "TITAN", "WIPRO", "ULTRACEMCO",
    "NESTLEIND", "POWERGRID", "NTPC", "TECHM", "TATAMOTORS",
    "INDUSINDBK", "JSWSTEEL", "GRASIM", "DIVISLAB", "DRREDDY",
    "CIPLA", "EICHERMOT", "COALINDIA", "BAJAJFINSV", "BPCL",
    "ONGC", "TATASTEEL", "BRITANNIA", "ADANIPORTS", "HINDALCO",
    "APOLLOHOSP", "SBILIFE", "BAJAJ-AUTO", "HDFCLIFE", "TATACONSUM",
    "PIDILITIND", "HEROMOTOCO", "M&M", "SHRIRAMFIN", "BEL",
]

# =============================================================================
# NIFTY NEXT 50 — Large-cap extension (completes NIFTY 100)
# =============================================================================
NIFTY_NEXT_50 = [
    "ADANIENT", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "ATGL",
    "BANKBARODA", "BERGEPAINT", "BOSCHLTD", "CANBK", "CHOLAFIN",
    "COLPAL", "DLF", "DABUR", "GODREJCP", "GAIL",
    "HAL", "HAVELLS", "ICICIPRULI", "INDIGO", "IOC",
    "IRCTC", "JINDALSTEL", "JIOFIN", "LICI", "LUPIN",
    "MARICO", "MOTHERSON", "NAUKRI", "NHPC", "OIL",
    "PEL", "PETRONET", "PFC", "PIIND", "PNB",
    "POLYCAB", "RECLTD", "SAIL", "SIEMENS", "SRF",
    "TATAPOWER", "TORNTPHARM", "TRENT", "TVSMOTOR", "UNITDSPR",
    "VBL", "VEDL", "ZOMATO", "ZYDUSLIFE", "YESBANK",
]

NIFTY_100 = NIFTY_50 + NIFTY_NEXT_50

# =============================================================================
# MIDCAP 100 — Growth exposure, YELLOW/GREEN only
# =============================================================================
MIDCAP_100 = [
    "ABCAPITAL", "ABFRL", "ACC", "ALKEM", "APLAPOLLO",
    "ASTRAL", "ATUL", "AUBANK", "BALKRISIND", "BANDHANBNK",
    "BATAINDIA", "BHARATFORG", "BHEL", "BIOCON", "CANFINHOME",
    "CENTRALBK", "CGPOWER", "CHAMBLFERT", "COFORGE", "CONCOR",
    "COROMANDEL", "CROMPTON", "CUMMINSIND", "DALBHARAT", "DEEPAKNTR",
    "DELHIVERY", "DEVYANI", "DIXON", "EMAMILTD", "ENDURANCE",
    "ESCORTS", "EXIDEIND", "FEDERALBNK", "GLAND", "GLAXO",
    "GNFC", "GODREJPROP", "GRANULES", "GUJGASLTD", "HDFCAMC",
    "HINDPETRO", "HONAUT", "IDFCFIRSTB", "IEX", "INDHOTEL",
    "INDUSTOWER", "IRFC", "JKCEMENT", "JUBLFOOD", "KAJARIACER",
    "KEI", "KPITTECH", "L&TFH", "LAURUSLABS", "LICHSGFIN",
    "LTIM", "LTTS", "M&MFIN", "MANAPPURAM", "MFSL",
    "MPHASIS", "MRF", "MUTHOOTFIN", "NAVINFLUOR", "NYKAA",
    "OBEROIRLTY", "OFSS", "PAGEIND", "PATANJALI", "PERSISTENT",
    "PHOENIXLTD", "POWERINDIA", "PRESTIGE", "PVRINOX", "RAJESHEXPO",
    "RAMCOCEM", "RELAXO", "SBICARD", "SCHAEFFLER", "SONACOMS",
    "STARHEALTH", "SUNDARMFIN", "SUNDRMFAST", "SUNTV", "SUPREMEIND",
    "SYNGENE", "TATACHEM", "TATACOMM", "TATAELXSI", "TATVA",
    "THERMAX", "TIMKEN", "TORNTPOWER", "TRIDENT", "UNIONBANK",
    "UPL", "VOLTAS", "WHIRLPOOL", "ZEEL", "3MINDIA",
]

# =============================================================================
# SMALLCAP 50 — Higher alpha potential, GREEN only
# =============================================================================
SMALLCAP_50 = [
    "AARTIIND", "AFFLE", "AJANTPHARM", "ANGELONE", "APTUS",
    "BSOFT", "BLS", "CAMPUS", "CAMS", "CDSL",
    "CENTURYPLY", "CESC", "CLEAN", "DATAPATTNS", "ECLERX",
    "ELGIEQUIP", "FINEORG", "FLUOROCHEM", "GILLETTE", "GRINDWELL",
    "HAPPSTMNDS", "HSCL", "IBREALEST", "INTELLECT", "JBCHEPHARM",
    "JWL", "KAYNES", "KIMS", "KSB", "LAXMIMACH",
    "MAPMYINDIA", "MASTEK", "MAXHEALTH", "MEDPLUS", "METROPOLIS",
    "NAM-INDIA", "OLECTRA", "POONAWALLA", "RADICO", "RATEGAIN",
    "ROUTE", "SAPPHIRE", "SOLARINDS", "SJVN", "SUZLON",
    "SWIGGY", "TIINDIA", "TRIVENI", "USHAMART", "ZENSARTECH",
]

# =============================================================================
# COMBINED LISTS
# =============================================================================
ALL_STOCKS = NIFTY_100 + MIDCAP_100 + SMALLCAP_50


# =============================================================================
# UNIVERSE SELECTOR
# =============================================================================

def get_raw_universe(risk_state: str = "GREEN") -> list[str]:
    """
    Return the candidate pool for a given risk state.

    GREEN  → All 250 stocks
    YELLOW → NIFTY 100 + top 50 midcaps (150)
    RED    → NIFTY 100 only (100)
    """
    if risk_state == "RED":
        pool = list(NIFTY_100)
        log.info(f"Universe: RED state — NIFTY 100 only ({len(pool)} stocks)")
        return pool

    if risk_state == "YELLOW":
        pool = list(NIFTY_100) + MIDCAP_100[:50]
        log.info(f"Universe: YELLOW state — {len(pool)} stocks")
        return pool

    full = list(ALL_STOCKS)[:MAX_UNIVERSE_SIZE]
    log.info(f"Universe: GREEN state — {len(full)} stocks")
    return full


def get_filtered_universe(risk_state: str = "GREEN",
                           apply_liquidity: bool = True) -> list[str]:
    """
    Return the universe after optional liquidity filtering.
    Set apply_liquidity=False during backtesting (data already validated).
    """
    candidates = get_raw_universe(risk_state)

    if not apply_liquidity:
        return candidates

    from config.settings import MIN_STOCK_PRICE_INR, MIN_30D_ADV_CRORE
    from data.price_provider import validate_liquidity

    passed, failed = [], []
    for sym in candidates:
        result = validate_liquidity(sym,
                                    min_price=MIN_STOCK_PRICE_INR,
                                    min_adv_crore=MIN_30D_ADV_CRORE)
        if result["passes"]:
            passed.append(sym)
        else:
            failed.append(sym)

    if failed:
        log.info(f"Liquidity filter removed {len(failed)} stocks")

    log.info(f"Final universe: {len(passed)} stocks")
    return passed


def is_nifty50(symbol: str) -> bool:
    """Return True if symbol is in NIFTY 50."""
    return symbol.upper().replace(".NS", "") in NIFTY_50


def is_nifty100(symbol: str) -> bool:
    """Return True if symbol is in NIFTY 100."""
    return symbol.upper().replace(".NS", "") in NIFTY_100


def get_tier(symbol: str) -> str:
    """Return the tier of a symbol: 'largecap', 'midcap', or 'smallcap'."""
    sym = symbol.upper().replace(".NS", "")
    if sym in NIFTY_100:
        return "largecap"
    if sym in MIDCAP_100:
        return "midcap"
    if sym in SMALLCAP_50:
        return "smallcap"
    return "unknown"