"""
backtest/backtester.py
-----------------------
Walk-forward backtesting engine.

Design principles:
  - ZERO lookahead bias: at day T, signals are computed using only data[0:T]
  - Realistic costs: brokerage (₹20/order), STT, slippage
  - Dynamic risk state: computed from simulated portfolio state each day
  - Sentiment: defaults to 0.5 (neutral) until Phase 3 adds FinBERT
  - Runs on any list of symbols with historical OHLCV data

Walk-forward structure:
  - Warm-up period: first WARMUP_DAYS used only for indicator seeding
  - Test period: remaining days, simulated day-by-day
  - At each day: check exits first, then generate entries

Usage:
    engine = BacktestEngine(symbols=["RELIANCE", "TCS", "INFY"],
                            start_date="2023-01-01",
                            end_date="2024-12-31",
                            initial_capital=100000)
    results = engine.run()
"""

import copy
from datetime import date, timedelta
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.settings import (
    BROKERAGE_PER_ORDER, STT_RATE_DELIVERY,
    EXCHANGE_TXN_RATE, SLIPPAGE_PCT,
    PRICE_LOOKBACK_DAYS,
)
from features.technicals import compute_all_signals
from features.preprocessing import clean_ohlcv
from features.corporate_actions import is_data_safe
from strategy.scoring import (
    compute_score, score_all_stocks, get_top_picks, compute_exit_levels,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT,
)
from strategy.signal_engine import (
    Position, generate_entry_signal, generate_exit_signal,
    BUY, SELL, HOLD,
)
from backtest.metrics import compute_all_metrics
from utils.logger import get_logger

log = get_logger("backtester")

WARMUP_DAYS = 60   # days of data used to seed indicators before trading starts


@dataclass
class BacktestConfig:
    symbols:         list[str]
    start_date:      str          # "YYYY-MM-DD"
    end_date:        str          # "YYYY-MM-DD"
    initial_capital: float = 100_000.0
    max_positions:   int   = 5    # max concurrent open positions
    sentiment_scores: dict = field(default_factory=dict)  # static fallback
    use_sentiment:   bool  = True  # enable dynamic sentiment proxy


class BacktestEngine:
    """
    Walk-forward backtesting engine.
    Call run() to execute and get results.
    """

    def __init__(self, config: BacktestConfig):
        self.cfg        = config
        self.cash       = config.initial_capital
        self.positions: dict[str, Position] = {}
        self.trades:    list[dict]           = []
        self.equity_curve: list[float]       = []
        self.daily_dates:  list[date]        = []
        self._price_data:  dict[str, pd.DataFrame] = {}
        self._risk_state   = "GREEN"
        self._daily_sentiment: dict[str, float] = {}  # updated each day

    # =========================================================================
    # DATA LOADING
    # =========================================================================

    def load_data(self) -> dict[str, pd.DataFrame]:
        """
        Load historical OHLCV for all symbols.
        Data is loaded once upfront — the simulation slices it day by day.
        Returns {symbol: clean_df} — failed symbols are dropped.
        """
        from data.price_provider import get_price_data

        start_dt = date.fromisoformat(self.cfg.start_date)
        end_dt   = date.fromisoformat(self.cfg.end_date)
        lookback_days = (date.today() - start_dt).days + WARMUP_DAYS + 30

        log.info(f"Loading data for {len(self.cfg.symbols)} symbols "
                 f"({self.cfg.start_date} → {self.cfg.end_date})")

        loaded = {}
        for sym in self.cfg.symbols:
            df = get_price_data(sym, lookback_days=lookback_days)
            if df is None:
                log.warning(f"No data for {sym} — skipping")
                continue
            cleaned = clean_ohlcv(df, sym)
            if cleaned is None:
                log.warning(f"Clean failed for {sym} — skipping")
                continue
            if not is_data_safe(cleaned, sym):
                log.warning(f"Data safety check failed for {sym} — skipping")
                continue
            # Restrict to backtest window + warmup
            start_with_warmup = start_dt - timedelta(days=WARMUP_DAYS + 10)
            mask = (cleaned.index.date >= start_with_warmup) & (cleaned.index.date <= end_dt)
            loaded[sym] = cleaned[mask]

        log.info(f"Data loaded: {len(loaded)}/{len(self.cfg.symbols)} symbols ready")
        self._price_data = loaded
        return loaded

    # =========================================================================
    # MAIN SIMULATION
    # =========================================================================

    def run(self) -> dict:
        """
        Execute the walk-forward backtest.

        Returns:
            {
              "metrics":       dict from compute_all_metrics(),
              "trades":        list of completed trade dicts,
              "equity_curve":  list of daily portfolio values,
              "daily_dates":   list of date objects (aligned with equity_curve),
              "config":        BacktestConfig summary,
            }
        """
        if not self._price_data:
            self.load_data()

        if not self._price_data:
            log.error("No price data available — aborting backtest")
            return {"error": "No price data"}

        # Build the sorted list of all trading dates in the test window
        start_dt = date.fromisoformat(self.cfg.start_date)
        end_dt   = date.fromisoformat(self.cfg.end_date)
        all_dates = self._get_trading_dates(start_dt, end_dt)

        if not all_dates:
            log.error("No trading dates in backtest window")
            return {"error": "No trading dates"}

        log.info(f"Backtest: {len(all_dates)} trading days, "
                 f"initial capital ₹{self.cfg.initial_capital:,.0f}")

        # Main simulation loop
        for current_date in all_dates:
            self._process_day(current_date)

        # Close any remaining open positions at last close price
        last_date = all_dates[-1]
        self._close_all_positions(last_date, reason="End of backtest")

        # Compute final metrics
        metrics = compute_all_metrics(
            self.equity_curve, self.trades, self.cfg.initial_capital
        )

        log.info(
            f"Backtest complete. Return={metrics['total_return_pct']:.1f}% "
            f"Sharpe={metrics['sharpe_ratio']:.2f} "
            f"MaxDD={metrics['max_drawdown_pct']:.1f}% "
            f"WinRate={metrics.get('win_rate', 0)*100:.1f}% "
            f"Verdict={metrics['go_nogo']}"
        )

        return {
            "metrics":      metrics,
            "trades":       self.trades,
            "equity_curve": self.equity_curve,
            "daily_dates":  self.daily_dates,
            "config": {
                "symbols":         self.cfg.symbols,
                "start_date":      self.cfg.start_date,
                "end_date":        self.cfg.end_date,
                "initial_capital": self.cfg.initial_capital,
            },
        }

    # =========================================================================
    # DAY PROCESSING
    # =========================================================================

    def _process_day(self, current_date: date):
        """Process one trading day: exits first, then entries."""

        # Step 1: Update risk state from current portfolio state
        portfolio_value = self._portfolio_value(current_date)
        self._risk_state = self._compute_risk_state(portfolio_value)

        # Step 1b: Compute daily sentiment from price proxy
        if self.cfg.use_sentiment:
            from backtest.sentiment_proxy import compute_universe_sentiment
            self._daily_sentiment = compute_universe_sentiment(
                self._price_data, current_date
            )
        else:
            self._daily_sentiment = self.cfg.sentiment_scores

        # Step 2: Check all open positions for exits
        for sym in list(self.positions.keys()):
            self._check_exit(sym, current_date)

        # Step 3: Generate entry signals if under max positions
        if len(self.positions) < self.cfg.max_positions:
            self._generate_entries(current_date)

        # Step 4: Record portfolio value for equity curve
        portfolio_value = self._portfolio_value(current_date)
        self.equity_curve.append(portfolio_value)
        self.daily_dates.append(current_date)

    def _check_exit(self, symbol: str, current_date: date):
        """Check and execute exit for a single open position."""
        position = self.positions.get(symbol)
        if position is None:
            return

        price = self._get_price(symbol, current_date)
        if price is None:
            return

        sentiment = self._daily_sentiment.get(symbol, 0.5)
        signal, reason = generate_exit_signal(
            position, price, current_date, self._risk_state, sentiment
        )

        if signal == SELL:
            self._execute_sell(symbol, position, price, current_date, reason)

    def _generate_entries(self, current_date: date):
        """Score all stocks and enter top picks that pass the threshold."""
        slots = self.cfg.max_positions - len(self.positions)
        if slots <= 0:
            return

        # Compute signals for all eligible symbols (not already held)
        stock_signals = {}
        for sym in self._price_data:
            if sym in self.positions:
                continue
            signals = self._compute_signals_up_to(sym, current_date)
            if signals is not None:
                stock_signals[sym] = signals

        if not stock_signals:
            return

        # Score and rank
        ranked = score_all_stocks(
            stock_signals,
            risk_state=self._risk_state,
            sentiment_scores=self._daily_sentiment,
        )
        picks = get_top_picks(ranked, self._risk_state, n=slots)

        for pick in picks:
            sym     = pick["symbol"]
            score   = pick["score"]
            signals = pick["signals"]

            signal = generate_entry_signal(
                sym, score, self._risk_state,
                has_open_position=sym in self.positions,
                signals=signals,
            )

            if signal == BUY:
                price = self._get_price(sym, current_date)
                if price and price > 0:
                    self._execute_buy(sym, price, current_date, signals, score)

    # =========================================================================
    # EXECUTION HELPERS
    # =========================================================================

    def _execute_buy(self, symbol: str, price: float, entry_date: date,
                      signals: dict, score: float):
        """Simulate a BUY order with realistic costs."""
        # Position sizing: risk-state-aware % of current cash
        size_pct = {"GREEN": 0.20, "YELLOW": 0.12, "RED": 0.06}.get(self._risk_state, 0.15)
        capital_for_trade = self.cash * size_pct

        # ATR-based position sizing
        atr = signals.get("atr_val", price * 0.02)
        stop_dist = max(2 * atr, price * STOP_LOSS_PCT.get(self._risk_state, 0.06))
        qty_by_risk = max(1, int(capital_for_trade / stop_dist))
        # Cap: never buy more than capital_for_trade worth of shares
        qty_by_capital = max(1, int(capital_for_trade / price))
        qty = min(qty_by_risk, qty_by_capital)
        cost = qty * price

        if cost > self.cash:
            qty  = max(1, int(self.cash * 0.95 / price))
            cost = qty * price

        if cost > self.cash or qty < 1:
            log.debug(f"Cannot buy {symbol}: insufficient cash "
                      f"(need ₹{cost:.0f}, have ₹{self.cash:.0f})")
            return

        # Apply slippage (buy at slightly higher price)
        fill_price = price * (1 + SLIPPAGE_PCT)

        # Transaction costs
        brokerage = BROKERAGE_PER_ORDER
        exchange_charge = cost * EXCHANGE_TXN_RATE
        total_cost = cost + brokerage + exchange_charge

        if total_cost > self.cash:
            return

        # Compute exit levels
        exits = compute_exit_levels(fill_price, atr, self._risk_state)

        # Open position
        self.positions[symbol] = Position(
            symbol=symbol,
            qty=qty,
            avg_entry_price=fill_price,
            entry_date=entry_date,
            stop_loss=exits["stop_loss"],
            take_profit=exits["take_profit"],
            trailing_stop=exits["stop_loss"],
            risk_state_at_entry=self._risk_state,
            highest_price_seen=fill_price,
        )
        self.cash -= total_cost

        log.debug(f"BUY  {symbol}: {qty} shares @ ₹{fill_price:.2f} "
                  f"(score={score:.3f}, cost=₹{total_cost:.0f}, "
                  f"SL=₹{exits['stop_loss']:.2f}, TP=₹{exits['take_profit']:.2f})")

    def _execute_sell(self, symbol: str, position: Position,
                       price: float, exit_date: date, reason: str):
        """Simulate a SELL order with realistic costs."""
        fill_price = price * (1 - SLIPPAGE_PCT)
        proceeds   = position.qty * fill_price

        # Costs: brokerage + STT (on sell side, delivery) + exchange charge
        brokerage      = BROKERAGE_PER_ORDER
        stt            = proceeds * STT_RATE_DELIVERY
        exchange_charge = proceeds * EXCHANGE_TXN_RATE
        total_cost     = brokerage + stt + exchange_charge

        net_proceeds = proceeds - total_cost
        entry_cost   = position.qty * position.avg_entry_price
        pnl          = net_proceeds - entry_cost

        holding_days = (exit_date - position.entry_date).days

        trade_record = {
            "symbol":       symbol,
            "action":       "SELL",
            "qty":          position.qty,
            "entry_price":  position.avg_entry_price,
            "exit_price":   fill_price,
            "entry_date":   position.entry_date.isoformat(),
            "exit_date":    exit_date.isoformat(),
            "holding_days": holding_days,
            "pnl":          round(pnl, 2),
            "pnl_pct":      round(pnl / entry_cost * 100, 2),
            "exit_reason":  reason,
            "risk_state":   position.risk_state_at_entry,
        }
        self.trades.append(trade_record)
        del self.positions[symbol]
        self.cash += net_proceeds

        emoji = "✓" if pnl > 0 else "✗"
        log.debug(f"SELL {symbol}: {position.qty} shares @ ₹{fill_price:.2f} "
                  f"PnL=₹{pnl:.0f} ({trade_record['pnl_pct']:+.1f}%) "
                  f"{emoji} Reason: {reason[:40]}")

    def _close_all_positions(self, close_date: date, reason: str):
        """Force-close all open positions (used at end of backtest)."""
        for sym in list(self.positions.keys()):
            price = self._get_price(sym, close_date)
            if price:
                self._execute_sell(sym, self.positions[sym], price, close_date, reason)

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _compute_signals_up_to(self, symbol: str, as_of_date: date) -> dict | None:
        """
        Compute technical signals using ONLY data available up to as_of_date.
        This is the critical no-lookahead-bias guarantee.
        """
        df = self._price_data.get(symbol)
        if df is None:
            return None
        slice_df = df[df.index.date <= as_of_date]
        if len(slice_df) < 55:
            return None
        return compute_all_signals(slice_df, symbol)

    def _get_price(self, symbol: str, as_of_date: date) -> float | None:
        """Get the closing price for a symbol on or before as_of_date."""
        df = self._price_data.get(symbol)
        if df is None:
            return None
        slice_df = df[df.index.date <= as_of_date]
        if slice_df.empty:
            return None
        return float(slice_df["Close"].iloc[-1])

    def _portfolio_value(self, as_of_date: date) -> float:
        """Total portfolio value: cash + market value of all open positions."""
        total = self.cash
        for sym, pos in self.positions.items():
            price = self._get_price(sym, as_of_date) or pos.avg_entry_price
            total += pos.qty * price
        return round(total, 2)

    def _compute_risk_state(self, portfolio_value: float) -> str:
        from config.settings import (
            DRAWDOWN_YELLOW_THRESH, DRAWDOWN_RED_THRESH,
            CASH_YELLOW_THRESH, CASH_RED_THRESH,
        )
        initial = self.cfg.initial_capital
        drawdown = (initial - portfolio_value) / initial if portfolio_value < initial else 0.0

        # Compare cash against CURRENT portfolio value, not initial capital
        # This way deploying capital doesn't fake a RED state
        cash_ratio = self.cash / portfolio_value if portfolio_value > 0 else 1.0

        if drawdown >= DRAWDOWN_RED_THRESH or cash_ratio <= CASH_RED_THRESH:
            return "RED"
        if drawdown >= DRAWDOWN_YELLOW_THRESH or cash_ratio <= CASH_YELLOW_THRESH:
            return "YELLOW"
        return "GREEN"

    def _get_trading_dates(self, start: date, end: date) -> list[date]:
        """Return all dates that appear in at least one loaded price dataset."""
        all_dates = set()
        for df in self._price_data.values():
            for d in df.index.date:
                if start <= d <= end:
                    all_dates.add(d)
        return sorted(all_dates)