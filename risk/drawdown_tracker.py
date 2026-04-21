"""
risk/drawdown_tracker.py
-------------------------
Real-time drawdown tracking for the paper trader.

Tracks:
  - Peak portfolio value (high water mark)
  - Current drawdown from peak
  - Daily loss vs daily limit
  - Consecutive losing days

Used by risk_manager.py to determine GREEN/YELLOW/RED state transitions.
"""

from datetime import date, datetime
from dataclasses import dataclass, field
from utils.logger import get_logger

log = get_logger("drawdown_tracker")


@dataclass
class DrawdownState:
    """Mutable state object for drawdown tracking."""
    initial_capital:      float
    peak_value:           float = 0.0
    current_value:        float = 0.0
    day_start_value:      float = 0.0
    current_drawdown_pct: float = 0.0
    daily_loss_pct:       float = 0.0
    consecutive_losses:   int   = 0
    peak_date:            str   = ""
    last_updated:         str   = ""

    def __post_init__(self):
        if self.peak_value == 0.0:
            self.peak_value = self.initial_capital
        if self.current_value == 0.0:
            self.current_value = self.initial_capital
        if self.day_start_value == 0.0:
            self.day_start_value = self.initial_capital


class DrawdownTracker:
    """
    Tracks portfolio drawdown in real time.

    Usage:
        tracker = DrawdownTracker(initial_capital=100_000)
        tracker.start_of_day(portfolio_value)
        tracker.update(new_portfolio_value)
        state = tracker.get_state()
    """

    def __init__(self, initial_capital: float):
        self.state = DrawdownState(initial_capital=initial_capital)
        log.info(f"DrawdownTracker initialized: capital=₹{initial_capital:,.0f}")

    def start_of_day(self, portfolio_value: float):
        """Call at the start of each trading day to record day-start value."""
        self.state.day_start_value = portfolio_value
        self.update(portfolio_value)
        log.debug(f"Day start: ₹{portfolio_value:,.0f}")

    def update(self, portfolio_value: float):
        """
        Update with the latest portfolio value.
        Recalculates drawdown from peak and daily loss.
        """
        now = datetime.utcnow().isoformat()
        self.state.current_value  = portfolio_value
        self.state.last_updated   = now

        # Update high water mark
        if portfolio_value > self.state.peak_value:
            self.state.peak_value = portfolio_value
            self.state.peak_date  = now[:10]

        # Drawdown from peak
        if self.state.peak_value > 0:
            self.state.current_drawdown_pct = (
                (self.state.peak_value - portfolio_value) / self.state.peak_value
            )
        else:
            self.state.current_drawdown_pct = 0.0

        # Daily loss
        if self.state.day_start_value > 0:
            self.state.daily_loss_pct = (
                (self.state.day_start_value - portfolio_value) / self.state.day_start_value
            )
        else:
            self.state.daily_loss_pct = 0.0

    def end_of_day(self, portfolio_value: float):
        """
        Call at end of trading day.
        Tracks consecutive losing days.
        """
        self.update(portfolio_value)
        if portfolio_value < self.state.day_start_value:
            self.state.consecutive_losses += 1
            log.info(f"Losing day #{self.state.consecutive_losses}: "
                     f"₹{self.state.day_start_value:,.0f} → ₹{portfolio_value:,.0f}")
        else:
            if self.state.consecutive_losses > 0:
                log.info(f"Winning day — reset consecutive losses "
                         f"(was {self.state.consecutive_losses})")
            self.state.consecutive_losses = 0

    def get_state(self) -> DrawdownState:
        return self.state

    def get_drawdown_pct(self) -> float:
        """Current drawdown from peak as a positive percentage (0.0–1.0)."""
        return max(0.0, self.state.current_drawdown_pct)

    def get_daily_loss_pct(self) -> float:
        """Today's loss as a positive percentage (0.0–1.0). 0 if day is up."""
        return max(0.0, self.state.daily_loss_pct)

    def is_daily_limit_hit(self, daily_limit_pct: float = 0.03) -> bool:
        """Return True if today's loss exceeds daily_limit_pct (default 3%)."""
        return self.get_daily_loss_pct() >= daily_limit_pct

    def summary(self) -> dict:
        s = self.state
        return {
            "current_value":        round(s.current_value, 2),
            "peak_value":           round(s.peak_value, 2),
            "drawdown_pct":         round(s.current_drawdown_pct * 100, 2),
            "daily_loss_pct":       round(s.daily_loss_pct * 100, 2),
            "consecutive_losses":   s.consecutive_losses,
            "peak_date":            s.peak_date,
        }