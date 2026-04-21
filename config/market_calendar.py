"""
config/market_calendar.py
--------------------------
NSE trading hours and holiday calendar for 2025–2026.
Used by the scheduler to gate cycle execution.
"""

from datetime import date, datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE trading session (IST)
MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Signal generation window — stop 30 min before close to avoid end-of-day noise
SIGNAL_CUTOFF = time(15, 0)

# -----------------------------------------------------------------------------
# NSE Holidays 2025
# Source: NSE India official holiday list
# -----------------------------------------------------------------------------
NSE_HOLIDAYS_2025 = {
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti / Ram Navami
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 23),  # Dussehra (Vijaya Dashami)
    date(2025, 11, 5),   # Diwali Laxmi Puja
    date(2025, 11, 11),  # Gurunanak Jayanti (tentative)
    date(2025, 12, 25),  # Christmas
}

# NSE Holidays 2026 (partial — update from NSE site when published)
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Mahashivratri (approx)
    date(2026, 4, 3),    # Good Friday (approx)
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 12, 25),  # Christmas
}

ALL_HOLIDAYS = NSE_HOLIDAYS_2025 | NSE_HOLIDAYS_2026


def is_trading_day(check_date: date = None) -> bool:
    """Return True if the given date is a valid NSE trading day."""
    if check_date is None:
        check_date = datetime.now(IST).date()
    # Weekends
    if check_date.weekday() >= 5:
        return False
    # Public holidays
    if check_date in ALL_HOLIDAYS:
        return False
    return True


def is_market_open(check_dt: datetime = None) -> bool:
    """Return True if market is currently open for trading."""
    if check_dt is None:
        check_dt = datetime.now(IST)
    # Ensure timezone-aware
    if check_dt.tzinfo is None:
        check_dt = IST.localize(check_dt)
    if not is_trading_day(check_dt.date()):
        return False
    current_time = check_dt.time().replace(tzinfo=None)
    return MARKET_OPEN <= current_time <= MARKET_CLOSE


def is_signal_window_open(check_dt: datetime = None) -> bool:
    """
    Return True if it's safe to generate and act on signals.
    Cuts off 30 minutes before market close to avoid end-of-day noise.
    """
    if check_dt is None:
        check_dt = datetime.now(IST)
    if check_dt.tzinfo is None:
        check_dt = IST.localize(check_dt)
    if not is_trading_day(check_dt.date()):
        return False
    current_time = check_dt.time().replace(tzinfo=None)
    return MARKET_OPEN <= current_time <= SIGNAL_CUTOFF


def next_market_open() -> datetime:
    """Return the datetime of the next market open in IST."""
    now = datetime.now(IST)
    candidate = now.date()
    # If today's open hasn't passed yet and it's a trading day, return today's open
    if is_trading_day(candidate):
        today_open = IST.localize(datetime.combine(candidate, MARKET_OPEN))
        if now < today_open:
            return today_open
    # Otherwise find the next trading day
    from datetime import timedelta
    candidate += timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return IST.localize(datetime.combine(candidate, MARKET_OPEN))


def get_market_status() -> dict:
    """Return a dict describing current market status — used by the UI."""
    now = datetime.now(IST)
    return {
        "timestamp": now.isoformat(),
        "is_trading_day": is_trading_day(now.date()),
        "is_market_open": is_market_open(now),
        "is_signal_window": is_signal_window_open(now),
        "next_open": next_market_open().isoformat(),
    }