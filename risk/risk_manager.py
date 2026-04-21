"""
risk/risk_manager.py
---------------------
Portfolio risk state machine: GREEN → YELLOW → RED

State is computed fresh each cycle from:
  - Current drawdown from peak
  - Cash ratio vs initial capital
  - Number of losing positions
  - Daily loss limit

State gates everything downstream:
  - Universe selection (RED → NIFTY 50 only)
  - Scoring weights (RED → heavily weight ATR/stability)
  - Buy thresholds (RED → much stricter)
  - Position sizing (RED → smallest)

Telegram alerts fire on state change and when RED is entered.
"""

import os
import requests
from dataclasses import dataclass
from datetime import datetime

from config.settings import (
    DRAWDOWN_YELLOW_THRESH, DRAWDOWN_RED_THRESH,
    CASH_YELLOW_THRESH, CASH_RED_THRESH,
)
from risk.drawdown_tracker import DrawdownTracker
from risk.exposure_limits import MAX_POSITIONS
from utils.db import write_control_flag, read_control_flag, log_activity
from utils.logger import get_logger

log = get_logger("risk_manager")

DAILY_LOSS_LIMIT_PCT = 0.04     # 4% daily loss triggers caution
DAILY_LOSS_RED_PCT   = 0.08     # 8% daily loss triggers RED immediately


@dataclass
class RiskContext:
    """Snapshot of all inputs used to compute risk state."""
    portfolio_value:    float
    initial_capital:    float
    cash:               float
    drawdown_pct:       float
    daily_loss_pct:     float
    open_position_count: int
    losing_position_count: int
    consecutive_losing_days: int


class RiskManager:
    """
    Computes and maintains the portfolio risk state.

    Usage:
        rm = RiskManager(initial_capital=100_000)
        state = rm.evaluate(portfolio_value, cash, open_positions, current_prices)
        # state is "GREEN", "YELLOW", or "RED"
    """

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.tracker         = DrawdownTracker(initial_capital)
        self._current_state  = "GREEN"
        self._previous_state = "GREEN"
        log.info(f"RiskManager initialized: capital=₹{initial_capital:,.0f}")

    def evaluate(self,
                 portfolio_value: float,
                 cash: float,
                 open_positions: dict = None,
                 current_prices: dict = None) -> str:
        """
        Compute current risk state from portfolio metrics.

        Args:
            portfolio_value: Total portfolio value (cash + positions)
            cash:            Uninvested cash
            open_positions:  {symbol: {"qty": int, "avg_entry_price": float}}
            current_prices:  {symbol: float}

        Returns:
            "GREEN", "YELLOW", or "RED"
        """
        if open_positions is None:
            open_positions = {}
        if current_prices is None:
            current_prices = {}

        # Update drawdown tracker
        self.tracker.update(portfolio_value)
        drawdown_pct  = self.tracker.get_drawdown_pct()
        daily_loss    = self.tracker.get_daily_loss_pct()

        # Cash ratio vs initial capital
        cash_ratio = cash / self.initial_capital if self.initial_capital > 0 else 1.0

        # Count losing positions
        losing = sum(
            1 for sym, pos in open_positions.items()
            if current_prices.get(sym, pos.get("avg_entry_price", 0))
            < pos.get("avg_entry_price", 0) * 0.995
        )

        ctx = RiskContext(
            portfolio_value=portfolio_value,
            initial_capital=self.initial_capital,
            cash=cash,
            drawdown_pct=drawdown_pct,
            daily_loss_pct=daily_loss,
            open_position_count=len(open_positions),
            losing_position_count=losing,
            consecutive_losing_days=self.tracker.state.consecutive_losses,
        )

        new_state = self._compute_state(ctx)
        self._handle_transition(new_state, ctx)
        self._current_state = new_state

        # Persist to DB so UI can read it
        write_control_flag("RISK_STATE", new_state)
        log_activity(
            f"Risk state: {new_state} | "
            f"DD={drawdown_pct*100:.1f}% | "
            f"Cash={cash_ratio*100:.0f}% | "
            f"Losers={losing}",
            level="INFO"
        )

        return new_state

    def _compute_state(self, ctx: RiskContext) -> str:
        """Apply state machine rules."""

        # RED conditions (any one triggers RED)
        if (ctx.drawdown_pct >= DRAWDOWN_RED_THRESH
                or ctx.cash / self.initial_capital <= CASH_RED_THRESH
                or ctx.daily_loss_pct >= DAILY_LOSS_RED_PCT
                or ctx.consecutive_losing_days >= 4):
            return "RED"

        # YELLOW conditions (any one triggers YELLOW)
        if (ctx.drawdown_pct >= DRAWDOWN_YELLOW_THRESH
                or ctx.cash / self.initial_capital <= CASH_YELLOW_THRESH
                or ctx.daily_loss_pct >= DAILY_LOSS_LIMIT_PCT
                or ctx.consecutive_losing_days >= 2
                or ctx.losing_position_count >= 3):
            return "YELLOW"

        return "GREEN"

    def _handle_transition(self, new_state: str, ctx: RiskContext):
        """Handle state transitions — log, alert, persist."""
        if new_state == self._current_state:
            return

        direction = "↑" if self._state_rank(new_state) < self._state_rank(self._current_state) else "↓"
        msg = (
            f"Risk state changed: {self._current_state} → {new_state} {direction} | "
            f"DD={ctx.drawdown_pct*100:.1f}% | "
            f"DailyLoss={ctx.daily_loss_pct*100:.1f}% | "
            f"Cash=₹{ctx.cash:,.0f} | "
            f"Losers={ctx.losing_position_count}"
        )
        log.warning(msg)
        log_activity(msg, level="WARNING")

        # Fire Telegram alert on any state change, especially RED entry
        if new_state == "RED":
            self._send_telegram_alert(
                f"🔴 RISK STATE → RED\n"
                f"Drawdown: {ctx.drawdown_pct*100:.1f}%\n"
                f"Daily Loss: {ctx.daily_loss_pct*100:.1f}%\n"
                f"Cash: ₹{ctx.cash:,.0f}\n"
                f"Losing Positions: {ctx.losing_position_count}\n"
                f"Consecutive Loss Days: {ctx.consecutive_losing_days}\n"
                f"Action: Switching to NIFTY 50 only, tightest filters"
            )
        elif self._current_state == "RED" and new_state != "RED":
            self._send_telegram_alert(
                f"🟡 Risk state recovering: RED → {new_state}\n"
                f"Drawdown: {ctx.drawdown_pct*100:.1f}%"
            )

        self._previous_state = self._current_state

    @staticmethod
    def _state_rank(state: str) -> int:
        return {"GREEN": 0, "YELLOW": 1, "RED": 2}.get(state, 1)

    @property
    def current_state(self) -> str:
        return self._current_state

    def start_of_day(self, portfolio_value: float):
        """Call at market open each day."""
        self.tracker.start_of_day(portfolio_value)
        log.info(f"Day start: portfolio=₹{portfolio_value:,.0f} "
                 f"state={self._current_state}")

    def end_of_day(self, portfolio_value: float):
        """Call at market close each day."""
        self.tracker.end_of_day(portfolio_value)

    def get_summary(self) -> dict:
        """Return a summary dict for the UI."""
        s = self.tracker.summary()
        return {
            **s,
            "risk_state":     self._current_state,
            "previous_state": self._previous_state,
            "initial_capital": self.initial_capital,
        }

    @staticmethod
    def _send_telegram_alert(message: str):
        """
        Send a Telegram message via bot API.
        Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment.
        Silently fails if not configured.
        """
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            log.debug("Telegram not configured — skipping alert")
            return
        try:
            url  = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
            if resp.status_code == 200:
                log.info("Telegram alert sent")
            else:
                log.warning(f"Telegram alert failed: {resp.status_code}")
        except Exception as e:
            log.warning(f"Telegram alert error: {e}")