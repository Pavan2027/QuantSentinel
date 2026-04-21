"""
strategy/signal_engine.py
--------------------------
Translates scores into BUY / SELL / HOLD decisions.
Also manages active position exit logic:
  - Hard stop loss
  - Take profit target
  - Trailing stop (activates after price moves in favour)
  - Time-based exit (max holding period per risk state)
  - Sentiment reversal exit
"""

from dataclasses import dataclass, field
from datetime import date

from strategy.scoring import BUY_THRESHOLD, MAX_HOLD_DAYS
from strategy.universe import is_nifty50
from utils.logger import get_logger

log = get_logger("signal_engine")

# Signal constants
BUY  = "BUY"
SELL = "SELL"
HOLD = "HOLD"


@dataclass
class Position:
    """Represents an open position."""
    symbol:               str
    qty:                  int
    avg_entry_price:      float
    entry_date:           date
    stop_loss:            float
    take_profit:          float
    trailing_stop:        float        # current trailing stop price
    trailing_activated:   bool = False
    risk_state_at_entry:  str = "GREEN"
    highest_price_seen:   float = field(default=0.0)

    def __post_init__(self):
        if self.highest_price_seen == 0.0:
            self.highest_price_seen = self.avg_entry_price

    @property
    def unrealized_pnl(self, current_price: float = 0.0) -> float:
        return (current_price - self.avg_entry_price) * self.qty

    def days_held(self, current_date: date) -> int:
        return (current_date - self.entry_date).days


def generate_entry_signal(symbol: str,
                           score: float,
                           risk_state: str,
                           has_open_position: bool,
                           signals: dict) -> str:
    """
    Decide whether to BUY a stock.

    Rules:
      - Never buy if already holding this stock
      - Score must exceed the risk-state threshold
      - RED state: only NIFTY 50 stocks eligible
      - EMA cross must be bullish (price trend confirmation)

    Returns: BUY or HOLD
    """
    if has_open_position:
        return HOLD

    threshold = BUY_THRESHOLD.get(risk_state, 0.65)

    if score < threshold:
        return HOLD
    
    # Require at least basic trend confirmation
    rsi_ok  = signals.get("rsi_score", 0) > 0.45
    macd_ok = signals.get("macd_score", 0) > 0.45
    ema_ok  = signals.get("ema_cross_bullish", False) or signals.get("price_above_ema20", False)
    mom_ok  = signals.get("momentum_score", 0) > 0.55

    quality_signals = sum([rsi_ok, macd_ok, ema_ok, mom_ok])
    if quality_signals < 3:
        log.debug(f"{symbol}: Skipped — insufficient signal quality ({quality_signals}/4)")
        return HOLD

    # RED state: restrict to NIFTY 50 only
    if risk_state == "RED" and not is_nifty50(symbol):
        log.debug(f"{symbol}: Skipped in RED state — not in NIFTY 50")
        return HOLD

    # Volume confirmation: avoid entering on very low volume
    if signals.get("volume_score", 0) < 0.20:
        log.debug(f"{symbol}: Skipped — volume too low")
        return HOLD

    log.debug(f"{symbol}: BUY signal generated (score={score:.3f}, state={risk_state})")
    return BUY


def generate_exit_signal(position: Position,
                          current_price: float,
                          current_date: date,
                          risk_state: str,
                          sentiment_score: float = 0.5) -> tuple[str, str]:
    """
    Check all exit conditions for an open position.

    Returns:
        (signal, reason) where signal is SELL or HOLD
        and reason describes which condition triggered.
    """
    max_hold = MAX_HOLD_DAYS.get(risk_state, 10)

    # --- 1. Hard stop loss ---
    if current_price <= position.stop_loss:
        return SELL, f"Stop loss hit (price={current_price:.2f} <= stop={position.stop_loss:.2f})"

    # --- 2. Take profit ---
    if current_price >= position.take_profit:
        return SELL, f"Take profit hit (price={current_price:.2f} >= target={position.take_profit:.2f})"

    # --- 3. Trailing stop ---
    # Update highest price seen
    if current_price > position.highest_price_seen:
        position.highest_price_seen = current_price

    # Activate trailing stop if price has moved enough above entry
    from strategy.scoring import TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT
    activation_price = position.avg_entry_price * (
        1 + TRAILING_STOP_ACTIVATION_PCT.get(position.risk_state_at_entry, 0.04)
    )
    if current_price >= activation_price:
        position.trailing_activated = True

    if position.trailing_activated:
        trail_dist = TRAILING_STOP_DISTANCE_PCT.get(position.risk_state_at_entry, 0.03)
        trailing_stop_price = position.highest_price_seen * (1 - trail_dist)
        position.trailing_stop = trailing_stop_price
        if current_price <= trailing_stop_price:
            return SELL, f"Trailing stop hit (price={current_price:.2f} <= trail={trailing_stop_price:.2f})"

    # --- 4. Sentiment reversal ---
    if sentiment_score < 0.3:
        return SELL, f"Sentiment reversal (score={sentiment_score:.2f} < 0.3)"

    # --- 5. Time-based exit ---
    days = position.days_held(current_date)
    if days >= max_hold:
        return SELL, f"Max holding period reached ({days}d >= {max_hold}d in {risk_state})"

    return HOLD, ""


def update_trailing_stop(position: Position,
                          current_price: float,
                          risk_state: str) -> Position:
    """Update position's trailing stop in-place. Called each cycle."""
    from strategy.scoring import TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT

    if current_price > position.highest_price_seen:
        position.highest_price_seen = current_price

    activation_price = position.avg_entry_price * (
        1 + TRAILING_STOP_ACTIVATION_PCT.get(risk_state, 0.04)
    )
    if current_price >= activation_price:
        position.trailing_activated = True

    if position.trailing_activated:
        trail_dist = TRAILING_STOP_DISTANCE_PCT.get(risk_state, 0.03)
        new_trail = position.highest_price_seen * (1 - trail_dist)
        if new_trail > position.trailing_stop:
            position.trailing_stop = new_trail

    return position