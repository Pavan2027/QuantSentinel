"""
execution/paper_trader.py
--------------------------
Paper trading engine — simulates live trading without real money.

Simulates realistically:
  - Brokerage: ₹20 flat per order (Upstox/Zerodha standard)
  - STT: 0.1% on sell side (delivery)
  - Exchange transaction charge: 0.00345%
  - Slippage: 0.05% per trade (market impact)

State is persisted to SQLite so the UI can read it live.
The paper trader is the primary testing environment before Upstox live.

Interface is designed to be identical to upstox_client.py —
swapping live trading in requires changing only the execution layer.
"""

from datetime import date, datetime
from dataclasses import dataclass, field
from typing import Optional

from config.settings import (
    PAPER_CAPITAL_INR,
    BROKERAGE_PER_ORDER,
    STT_RATE_DELIVERY,
    EXCHANGE_TXN_RATE,
    SLIPPAGE_PCT,
)
from utils.db import get_conn, init_db, log_activity
from utils.logger import get_logger, log_trade

log = get_logger("paper_trader")


@dataclass
class PaperPosition:
    symbol:           str
    qty:              int
    avg_entry_price:  float
    entry_date:       str
    stop_loss:        float
    take_profit:      float
    trailing_stop:    float
    risk_state:       str = "GREEN"
    highest_price:    float = 0.0

    def __post_init__(self):
        if self.highest_price == 0.0:
            self.highest_price = self.avg_entry_price

    @property
    def entry_value(self) -> float:
        return self.qty * self.avg_entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.avg_entry_price) * self.qty

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.avg_entry_price == 0:
            return 0.0
        return (current_price - self.avg_entry_price) / self.avg_entry_price


class PaperTrader:
    """
    Full paper trading engine.

    Usage:
        trader = PaperTrader()
        trader.buy("RELIANCE", price=2840.0, qty=5, stop_loss=2700.0,
                   take_profit=3100.0, risk_state="GREEN")
        trader.sell("RELIANCE", price=2950.0, reason="Take profit hit")
        summary = trader.get_portfolio_summary(current_prices)
    """

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or PAPER_CAPITAL_INR
        init_db()
        self._load_state()
        log.info(
            f"PaperTrader initialized: "
            f"cash=₹{self.cash:,.0f} | "
            f"positions={len(self.positions)}"
        )

    # =========================================================================
    # STATE PERSISTENCE
    # =========================================================================

    def _load_state(self):
        """Load paper trading state from SQLite. Initialize if first run."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM control_flags WHERE key = 'PAPER_CASH'"
            ).fetchone()
            if row:
                self.cash = float(row["value"])
            else:
                self.cash = self.initial_capital
                conn.execute(
                    "INSERT OR REPLACE INTO control_flags (key, value, updated_at) "
                    "VALUES ('PAPER_CASH', ?, ?)",
                    (str(self.cash), datetime.utcnow().isoformat())
                )

            # Load open positions
            rows = conn.execute("SELECT * FROM positions").fetchall()
            self.positions: dict[str, PaperPosition] = {}
            for r in rows:
                self.positions[r["symbol"]] = PaperPosition(
                    symbol=r["symbol"],
                    qty=r["qty"],
                    avg_entry_price=r["avg_entry_price"],
                    entry_date=r["entry_date"],
                    stop_loss=r["stop_loss"],
                    take_profit=r["take_profit"],
                    trailing_stop=r["trailing_stop"],
                    risk_state=r["risk_state_at_entry"] if "risk_state_at_entry" in r.keys() else "GREEN",
                    highest_price=r["avg_entry_price"],
                )

    def _save_cash(self):
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO control_flags (key, value, updated_at) "
                "VALUES ('PAPER_CASH', ?, ?)",
                (str(round(self.cash, 2)), datetime.utcnow().isoformat())
            )

    def _save_position(self, pos: PaperPosition):
        with get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO positions
                (symbol, qty, avg_entry_price, stop_loss, take_profit,
                 trailing_stop, entry_date, risk_state_at_entry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (pos.symbol, pos.qty, pos.avg_entry_price,
                  pos.stop_loss, pos.take_profit, pos.trailing_stop,
                  pos.entry_date, pos.risk_state))

    def _delete_position(self, symbol: str):
        with get_conn() as conn:
            conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

    def _save_trade(self, trade: dict):
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO trades
                (symbol, action, qty, price, brokerage, slippage,
                 reason, risk_state, pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade["symbol"], trade["action"], trade["qty"],
                trade["price"], trade["brokerage"], trade["slippage"],
                trade.get("reason", ""), trade.get("risk_state", "GREEN"),
                trade.get("pnl"), datetime.utcnow().isoformat()
            ))

    # =========================================================================
    # ORDER EXECUTION
    # =========================================================================

    def buy(self,
            symbol: str,
            price: float,
            qty: int,
            stop_loss: float,
            take_profit: float,
            risk_state: str = "GREEN",
            reason: str = "Signal") -> dict:
        """
        Execute a paper BUY order.

        Returns:
            {"success": bool, "fill_price": float, "total_cost": float,
             "reason": str}
        """
        if symbol in self.positions:
            return {"success": False, "reason": f"Already holding {symbol}"}
        if qty < 1:
            return {"success": False, "reason": "Quantity must be >= 1"}

        # Slippage: buy at slightly higher price
        fill_price = round(price * (1 + SLIPPAGE_PCT), 2)
        trade_value = fill_price * qty

        # Transaction costs
        brokerage      = BROKERAGE_PER_ORDER
        exchange_charge = trade_value * EXCHANGE_TXN_RATE
        total_cost     = trade_value + brokerage + exchange_charge

        if total_cost > self.cash:
            return {
                "success": False,
                "reason":  f"Insufficient cash: need ₹{total_cost:,.0f}, "
                           f"have ₹{self.cash:,.0f}",
            }

        # Execute
        self.cash -= total_cost
        position = PaperPosition(
            symbol=symbol,
            qty=qty,
            avg_entry_price=fill_price,
            entry_date=date.today().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=stop_loss,
            risk_state=risk_state,
            highest_price=fill_price,
        )
        self.positions[symbol] = position

        # Persist
        self._save_position(position)
        self._save_cash()
        self._save_trade({
            "symbol": symbol, "action": "BUY", "qty": qty,
            "price": fill_price, "brokerage": brokerage,
            "slippage": round(price * SLIPPAGE_PCT * qty, 2),
            "reason": reason, "risk_state": risk_state, "pnl": None,
        })

        log_trade("BUY", symbol, qty, fill_price, reason)
        log_activity(
            f"BUY {symbol}: {qty} @ ₹{fill_price:.2f} | "
            f"SL=₹{stop_loss:.2f} TP=₹{take_profit:.2f} | "
            f"Cost=₹{total_cost:,.0f} | Cash left=₹{self.cash:,.0f}",
            level="INFO"
        )

        return {
            "success":     True,
            "fill_price":  fill_price,
            "total_cost":  round(total_cost, 2),
            "cash_after":  round(self.cash, 2),
            "reason":      "OK",
        }

    def sell(self,
             symbol: str,
             price: float,
             reason: str = "Signal") -> dict:
        """
        Execute a paper SELL order for an open position.

        Returns:
            {"success": bool, "pnl": float, "pnl_pct": float, "reason": str}
        """
        position = self.positions.get(symbol)
        if position is None:
            return {"success": False, "reason": f"No open position for {symbol}"}

        # Slippage: sell at slightly lower price
        fill_price  = round(price * (1 - SLIPPAGE_PCT), 2)
        proceeds    = fill_price * position.qty

        # Costs on sell side: brokerage + STT + exchange
        brokerage      = BROKERAGE_PER_ORDER
        stt            = proceeds * STT_RATE_DELIVERY
        exchange_charge = proceeds * EXCHANGE_TXN_RATE
        total_deductions = brokerage + stt + exchange_charge

        net_proceeds = proceeds - total_deductions
        entry_cost   = position.qty * position.avg_entry_price
        pnl          = net_proceeds - entry_cost
        pnl_pct      = pnl / entry_cost if entry_cost > 0 else 0

        holding_days = (
            date.today() - date.fromisoformat(position.entry_date)
        ).days

        # Execute
        self.cash += net_proceeds
        del self.positions[symbol]

        # Persist
        self._delete_position(symbol)
        self._save_cash()
        self._save_trade({
            "symbol": symbol, "action": "SELL", "qty": position.qty,
            "price": fill_price, "brokerage": brokerage,
            "slippage": round(price * SLIPPAGE_PCT * position.qty, 2),
            "reason": reason, "risk_state": position.risk_state,
            "pnl": round(pnl, 2),
        })

        emoji = "✅" if pnl > 0 else "❌"
        log_trade("SELL", symbol, position.qty, fill_price, reason, pnl)
        log_activity(
            f"SELL {symbol}: {position.qty} @ ₹{fill_price:.2f} | "
            f"PnL=₹{pnl:+,.0f} ({pnl_pct*100:+.1f}%) {emoji} | "
            f"Held {holding_days}d | {reason}",
            level="INFO"
        )

        return {
            "success":    True,
            "pnl":        round(pnl, 2),
            "pnl_pct":    round(pnl_pct * 100, 2),
            "fill_price": fill_price,
            "holding_days": holding_days,
            "reason":     "OK",
        }

    def update_trailing_stop(self, symbol: str, current_price: float,
                              risk_state: str = "GREEN"):
        """Update trailing stop for an open position as price moves up."""
        from strategy.scoring import (
            TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT
        )
        pos = self.positions.get(symbol)
        if not pos:
            return

        if current_price > pos.highest_price:
            pos.highest_price = current_price

        activation = pos.avg_entry_price * (
            1 + TRAILING_STOP_ACTIVATION_PCT.get(pos.risk_state, 0.04)
        )
        if current_price >= activation:
            trail_dist = TRAILING_STOP_DISTANCE_PCT.get(pos.risk_state, 0.03)
            new_trail  = pos.highest_price * (1 - trail_dist)
            if new_trail > pos.trailing_stop:
                log.debug(f"{symbol}: Trailing stop updated "
                          f"₹{pos.trailing_stop:.2f} → ₹{new_trail:.2f}")
                pos.trailing_stop = new_trail
                self._save_position(pos)

    # =========================================================================
    # PORTFOLIO REPORTING
    # =========================================================================

    def get_portfolio_summary(self, current_prices: dict = None) -> dict:
        """
        Return a full portfolio snapshot for the UI.

        Args:
            current_prices: {symbol: float} latest prices for open positions
        """
        if current_prices is None:
            current_prices = {}

        positions_data = []
        total_market_value = 0.0
        total_unrealized_pnl = 0.0

        for sym, pos in self.positions.items():
            cp = current_prices.get(sym, pos.avg_entry_price)
            upnl = pos.unrealized_pnl(cp)
            upnl_pct = pos.unrealized_pnl_pct(cp) * 100
            mv = pos.qty * cp
            total_market_value   += mv
            total_unrealized_pnl += upnl

            positions_data.append({
                "symbol":          sym,
                "qty":             pos.qty,
                "entry_price":     round(pos.avg_entry_price, 2),
                "current_price":   round(cp, 2),
                "market_value":    round(mv, 2),
                "unrealized_pnl":  round(upnl, 2),
                "unrealized_pct":  round(upnl_pct, 2),
                "stop_loss":       round(pos.stop_loss, 2),
                "take_profit":     round(pos.take_profit, 2),
                "trailing_stop":   round(pos.trailing_stop, 2),
                "entry_date":      pos.entry_date,
                "risk_state":      pos.risk_state,
            })

        total_value = self.cash + total_market_value
        total_return_pct = (
            (total_value - self.initial_capital) / self.initial_capital * 100
            if self.initial_capital > 0 else 0
        )

        return {
            "cash":                round(self.cash, 2),
            "invested":            round(total_market_value, 2),
            "total_value":         round(total_value, 2),
            "initial_capital":     self.initial_capital,
            "total_return_pct":    round(total_return_pct, 2),
            "unrealized_pnl":      round(total_unrealized_pnl, 2),
            "open_positions":      len(self.positions),
            "positions":           positions_data,
        }

    def get_realized_pnl(self) -> dict:
        """Sum all completed trades from the DB."""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT pnl FROM trades WHERE action='SELL' AND pnl IS NOT NULL"
            ).fetchall()

        total = sum(r["pnl"] for r in rows)
        wins  = sum(1 for r in rows if r["pnl"] > 0)
        total_trades = len(rows)
        win_rate = wins / total_trades if total_trades > 0 else 0.0

        return {
            "realized_pnl":  round(total, 2),
            "total_trades":  total_trades,
            "winning_trades": wins,
            "losing_trades": total_trades - wins,
            "win_rate":      round(win_rate * 100, 1),
        }

    def reset(self, confirm: bool = False):
        """
        Reset paper trading state to initial capital.
        Requires confirm=True to prevent accidental resets.
        """
        if not confirm:
            log.warning("Reset called without confirm=True — ignored")
            return False
        self.cash = self.initial_capital
        self.positions = {}
        with get_conn() as conn:
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM trades")
            conn.execute(
                "INSERT OR REPLACE INTO control_flags (key, value, updated_at) "
                "VALUES ('PAPER_CASH', ?, ?)",
                (str(self.initial_capital), datetime.utcnow().isoformat())
            )
        log.info(f"Paper trader reset to ₹{self.initial_capital:,.0f}")
        log_activity("⚠️ Paper trader RESET to initial capital", level="WARNING")
        return True