"""
risk/exposure_limits.py
------------------------
Portfolio exposure limits and validation.

Rules enforced:
  - Max % of portfolio in a single stock
  - Max % of portfolio in a single sector
  - Minimum cash floor (never fully invest)
  - Max concurrent open positions per risk state
  - Hard stop: no new BUYs if 3+ losing positions open

Sector mapping covers NSE large/mid caps.
"""

from config.settings import (
    DRAWDOWN_YELLOW_THRESH, DRAWDOWN_RED_THRESH,
    CASH_YELLOW_THRESH, CASH_RED_THRESH,
)
from utils.logger import get_logger

log = get_logger("exposure_limits")

# Per-stock position cap as % of total portfolio value
MAX_POSITION_PCT = {
    "GREEN":  0.20,   # max 20% in any single stock
    "YELLOW": 0.12,
    "RED":    0.06,
}

# Minimum cash as % of initial capital (never go below this)
MIN_CASH_FLOOR_PCT = 0.15   # always keep at least 15% as cash

# Max concurrent positions
MAX_POSITIONS = {
    "GREEN":  5,
    "YELLOW": 3,
    "RED":    2,
}

# No new BUYs if this many losing positions are open
MAX_LOSING_POSITIONS_FOR_NEW_BUY = 3

# Sector map for NSE stocks
SECTOR_MAP = {
    # Technology
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "MPHASIS": "IT", "COFORGE": "IT", "LTTS": "IT",
    "PERSISTENT": "IT", "TATAELXSI": "IT", "OFSS": "IT",

    # Banking
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "KOTAKBANK": "Banking",
    "SBIN": "Banking", "AXISBANK": "Banking", "INDUSINDBK": "Banking",
    "FEDERALBNK": "Banking", "BANDHANBNK": "Banking", "IDFCFIRSTB": "Banking",
    "AUBANK": "Banking",

    # Finance / NBFC
    "BAJFINANCE": "Finance", "BAJAJFINSV": "Finance", "CHOLAFIN": "Finance",
    "MUTHOOTFIN": "Finance", "MANAPPURAM": "Finance", "LICHSGFIN": "Finance",
    "SHRIRAMFIN": "Finance", "ABCAPITAL": "Finance",

    # Energy / Oil & Gas
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy",
    "HINDPETRO": "Energy", "GUJGASLTD": "Energy", "PETRONET": "Energy",

    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "MARICO": "FMCG", "COLPAL": "FMCG",
    "TATACONSUM": "FMCG", "GODREJCP": "FMCG", "DABUR": "FMCG",

    # Pharma
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "LUPIN": "Pharma", "GRANULES": "Pharma",
    "GLAND": "Pharma", "PIIND": "Pharma",

    # Auto
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "BAJAJ-AUTO": "Auto",
    "EICHERMOT": "Auto", "HEROMOTOCO": "Auto", "TVSMOTOR": "Auto",
    "M&M": "Auto", "ESCORTS": "Auto",

    # Metals / Mining
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "COALINDIA": "Metals", "SAIL": "Metals", "NMDC": "Metals",

    # Infrastructure / Capital Goods
    "LT": "Infra", "BEL": "Infra", "HAL": "Infra",
    "ADANIPORTS": "Infra", "CONCOR": "Infra", "SIEMENS": "Infra",

    # Consumer / Retail
    "TITAN": "Consumer", "ASIANPAINT": "Consumer", "PIDILITIND": "Consumer",
    "BERGEPAINT": "Consumer", "TRENT": "Consumer",

    # Telecom
    "BHARTIARTL": "Telecom",

    # Power / Utilities
    "POWERGRID": "Utilities", "NTPC": "Utilities",

    # Cement
    "ULTRACEMCO": "Cement", "GRASIM": "Cement", "DALBHARAT": "Cement",
    "JKCEMENT": "Cement", "RAMCOCEM": "Cement",

    # Insurance
    "SBILIFE": "Insurance", "HDFCLIFE": "Insurance", "LICI": "Insurance",
    "STARHEALTH": "Insurance",
}

MAX_SECTOR_PCT = {
    "GREEN":  0.40,   # max 40% in any single sector
    "YELLOW": 0.30,
    "RED":    0.20,
}


def get_sector(symbol: str) -> str:
    """Return sector for a symbol. 'Other' if not mapped."""
    return SECTOR_MAP.get(symbol.upper().replace(".NS", ""), "Other")


def check_position_size_limit(symbol: str,
                               trade_value: float,
                               portfolio_value: float,
                               risk_state: str) -> dict:
    """
    Check if a proposed trade value exceeds per-stock limit.

    Returns:
        {"allowed": bool, "max_value": float, "reason": str}
    """
    max_pct = MAX_POSITION_PCT.get(risk_state, 0.15)
    max_value = portfolio_value * max_pct

    if trade_value > max_value:
        return {
            "allowed":   False,
            "max_value": round(max_value, 2),
            "reason":    f"{symbol}: trade ₹{trade_value:,.0f} exceeds "
                         f"{max_pct*100:.0f}% limit (₹{max_value:,.0f})",
        }
    return {"allowed": True, "max_value": round(max_value, 2), "reason": "OK"}


def check_sector_exposure(symbol: str,
                           trade_value: float,
                           open_positions: dict,
                           portfolio_value: float,
                           risk_state: str) -> dict:
    """
    Check if adding this position would breach sector concentration limit.

    Args:
        open_positions: {symbol: {"qty": int, "avg_entry_price": float}}
    """
    sector = get_sector(symbol)
    max_pct = MAX_SECTOR_PCT.get(risk_state, 0.35)

    # Current sector exposure
    current_exposure = 0.0
    for sym, pos in open_positions.items():
        if get_sector(sym) == sector:
            current_exposure += pos.get("qty", 0) * pos.get("avg_entry_price", 0)

    new_exposure = current_exposure + trade_value
    new_pct = new_exposure / portfolio_value if portfolio_value > 0 else 0

    if new_pct > max_pct:
        return {
            "allowed": False,
            "sector":  sector,
            "current_pct": round(current_exposure / portfolio_value * 100, 1),
            "would_be_pct": round(new_pct * 100, 1),
            "max_pct": max_pct * 100,
            "reason":  f"Sector '{sector}' would reach {new_pct*100:.1f}% "
                       f"(limit: {max_pct*100:.0f}%)",
        }
    return {
        "allowed": True, "sector": sector,
        "current_pct": round(current_exposure / portfolio_value * 100, 1),
        "would_be_pct": round(new_pct * 100, 1),
        "reason": "OK",
    }


def check_cash_floor(cash: float, initial_capital: float) -> dict:
    """Check if cash is above the minimum floor."""
    floor = initial_capital * MIN_CASH_FLOOR_PCT
    if cash < floor:
        return {
            "allowed": False,
            "cash": round(cash, 2),
            "floor": round(floor, 2),
            "reason": f"Cash ₹{cash:,.0f} below minimum floor ₹{floor:,.0f}",
        }
    return {"allowed": True, "cash": round(cash, 2), "floor": round(floor, 2), "reason": "OK"}


def check_losing_positions(open_positions: dict,
                             current_prices: dict) -> dict:
    """
    Count open positions currently at a loss.
    Blocks new BUYs if too many losers are open.
    """
    losers = 0
    for sym, pos in open_positions.items():
        current = current_prices.get(sym, pos.get("avg_entry_price", 0))
        entry   = pos.get("avg_entry_price", 0)
        # Add a 0.5% buffer — don't count as losing unless meaningfully down
        if current < entry * 0.995:
            losers += 1

    blocked = losers >= MAX_LOSING_POSITIONS_FOR_NEW_BUY
    return {
        "allowed":      not blocked,
        "losing_count": losers,
        "limit":        MAX_LOSING_POSITIONS_FOR_NEW_BUY,
        "reason":       (f"Too many losing positions ({losers}/{MAX_LOSING_POSITIONS_FOR_NEW_BUY})"
                         if blocked else "OK"),
    }


def validate_new_buy(symbol: str,
                      trade_value: float,
                      cash: float,
                      portfolio_value: float,
                      initial_capital: float,
                      open_positions: dict,
                      current_prices: dict,
                      risk_state: str) -> dict:
    """
    Run all exposure checks before allowing a new BUY.

    Returns:
        {"allowed": bool, "failures": [str], "checks": dict}
    """
    failures = []
    checks = {}

    # 1. Position size
    size_check = check_position_size_limit(
        symbol, trade_value, portfolio_value, risk_state
    )
    checks["position_size"] = size_check
    if not size_check["allowed"]:
        failures.append(size_check["reason"])

    # 2. Sector exposure
    sector_check = check_sector_exposure(
        symbol, trade_value, open_positions, portfolio_value, risk_state
    )
    checks["sector"] = sector_check
    if not sector_check["allowed"]:
        failures.append(sector_check["reason"])

    # 3. Cash floor
    cash_check = check_cash_floor(cash - trade_value, initial_capital)
    checks["cash_floor"] = cash_check
    if not cash_check["allowed"]:
        failures.append(cash_check["reason"])

    # 4. Max positions
    max_pos = MAX_POSITIONS.get(risk_state, 3)
    if len(open_positions) >= max_pos:
        reason = f"Max positions reached ({len(open_positions)}/{max_pos})"
        failures.append(reason)
        checks["max_positions"] = {"allowed": False, "reason": reason}
    else:
        checks["max_positions"] = {"allowed": True, "reason": "OK"}

    # 5. Losing positions gate
    loser_check = check_losing_positions(open_positions, current_prices)
    checks["losing_gate"] = loser_check
    if not loser_check["allowed"]:
        failures.append(loser_check["reason"])

    allowed = len(failures) == 0
    if not allowed:
        log.info(f"BUY blocked for {symbol}: {'; '.join(failures)}")

    return {"allowed": allowed, "failures": failures, "checks": checks}