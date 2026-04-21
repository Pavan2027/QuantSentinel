"""
execution/upstox_client.py
---------------------------
Live trading via Upstox API v3.

CRITICAL: This executes real orders with real money.
Only use after:
  ✅ Paper trading stable for 2+ weeks
  ✅ Upstox sandbox tested successfully
  ✅ LIVE_TRADING=true explicitly set in .env

Interface is intentionally identical to PaperTrader so swapping
requires changing only one line in main.py.

Key differences from paper_trader:
  - Requires valid Upstox access token (refreshed daily)
  - Orders go to NSE via Upstox infrastructure
  - Fills may differ from requested price (market orders)
  - Rate limits apply (10 orders/second, 10000/day)
  - Only works during market hours (9:15 AM – 3:30 PM IST)
"""

import os
import time
from datetime import date, datetime, timezone
from typing import Optional

from utils.db import get_conn, init_db, log_activity
from utils.logger import get_logger, log_trade
from config.settings import (
    BROKERAGE_PER_ORDER, STT_RATE_DELIVERY,
    EXCHANGE_TXN_RATE, PAPER_CAPITAL_INR,
)

log = get_logger("upstox_client")

# Safety gate — must be explicitly enabled
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING", "false").lower() == "true"

# Upstox instrument token format: "NSE_EQ|{isin}"
# We maintain a mapping from symbol to instrument token
# Full list: https://upstox.com/developer/api-documentation/instruments/
INSTRUMENT_MAP = {
    "RELIANCE":   "NSE_EQ|INE002A01018",
    "TCS":        "NSE_EQ|INE467B01029",
    "HDFCBANK":   "NSE_EQ|INE040A01034",
    "INFY":       "NSE_EQ|INE009A01021",
    "ICICIBANK":  "NSE_EQ|INE090A01021",
    "SBIN":       "NSE_EQ|INE062A01020",
    "AXISBANK":   "NSE_EQ|INE238A01034",
    "KOTAKBANK":  "NSE_EQ|INE237A01028",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "ITC":        "NSE_EQ|INE154A01025",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "TITAN":      "NSE_EQ|INE280A01028",
    "WIPRO":      "NSE_EQ|INE075A01022",
    "HCLTECH":    "NSE_EQ|INE860A01027",
    "SUNPHARMA":  "NSE_EQ|INE044A01036",
    "MARUTI":     "NSE_EQ|INE585B01010",
    "BAJFINANCE": "NSE_EQ|INE296A01024",
    "LT":         "NSE_EQ|INE018A01030",
    "NTPC":       "NSE_EQ|INE733E01010",
    "POWERGRID":  "NSE_EQ|INE752E01010",
    "BAJAJFINSV": "NSE_EQ|INE918I01026",
    "COLPAL":     "NSE_EQ|INE259A01022",
    "GUJGASLTD":  "NSE_EQ|INE844O01030",
    "IRFC":       "NSE_EQ|INE053F01010",
    "BALKRISIND": "NSE_EQ|INE787D01026",
    "ASIANPAINT": "NSE_EQ|INE021A01026",
    "NESTLEIND":  "NSE_EQ|INE239A01024",
    "ULTRACEMCO": "NSE_EQ|INE481G01011",
    "TECHM":      "NSE_EQ|INE669C01036",
    # Add more as needed from Upstox instruments file
}


def _get_instrument_token(symbol: str) -> str | None:
    """Get Upstox instrument token for a symbol."""
    token = INSTRUMENT_MAP.get(symbol.upper().replace(".NS", ""))
    if not token:
        log.warning(f"No instrument token for {symbol} — add to INSTRUMENT_MAP")
    return token


def _get_upstox_client():
    """Initialize Upstox SDK client with valid token."""
    try:
        import upstox_client
    except ImportError:
        raise ImportError(
            "upstox-python-sdk not installed. Run: pip install upstox-python-sdk"
        )

    from execution.upstox_auth import get_valid_token
    token = get_valid_token()

    use_sandbox = os.getenv("UPSTOX_SANDBOX", "true").lower() == "true"

    configuration = upstox_client.Configuration(sandbox=use_sandbox)
    configuration.access_token = token

    client = upstox_client.ApiClient(configuration)
    log.info(f"Upstox client initialized ({'SANDBOX' if use_sandbox else 'LIVE'})")
    return client


class UpstoxClient:
    """
    Live trading client with identical interface to PaperTrader.

    SAFE MODE: When LIVE_TRADING=false (default), all orders are
    logged but NOT sent to Upstox. Flip to true only when ready.
    """

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or PAPER_CAPITAL_INR
        init_db()
        self._load_state()

        mode = "LIVE" if LIVE_TRADING_ENABLED else "DRY-RUN (set LIVE_TRADING=true to enable)"
        log.info(f"UpstoxClient initialized | Mode: {mode}")

        if LIVE_TRADING_ENABLED:
            log.warning("⚠️  LIVE TRADING ENABLED — real money at risk")
            log_activity("⚠️ LIVE TRADING MODE ACTIVE", level="WARNING")
        else:
            log.info("DRY-RUN mode — orders logged but not sent to Upstox")

    def _load_state(self):
        """Load portfolio state from DB (shared with paper trader schema)."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM control_flags WHERE key = 'UPSTOX_CASH'"
            ).fetchone()
            if row:
                self.cash = float(row["value"])
            else:
                self.cash = self.initial_capital
                conn.execute("""
                    INSERT OR REPLACE INTO control_flags (key, value, updated_at)
                    VALUES ('UPSTOX_CASH', ?, ?)
                """, (str(self.cash), datetime.now(timezone.utc).isoformat()))

            rows = conn.execute("SELECT * FROM positions").fetchall()
            self.positions = {}
            from execution.paper_trader import PaperPosition
            for r in rows:
                self.positions[r["symbol"]] = PaperPosition(
                    symbol=r["symbol"],
                    qty=r["qty"],
                    avg_entry_price=r["avg_entry_price"],
                    entry_date=r["entry_date"],
                    stop_loss=r["stop_loss"],
                    take_profit=r["take_profit"],
                    trailing_stop=r["trailing_stop"],
                    risk_state=r["risk_state_at_entry"]
                    if "risk_state_at_entry" in r.keys() else "GREEN",
                )

    def _save_cash(self):
        with get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO control_flags (key, value, updated_at)
                VALUES ('UPSTOX_CASH', ?, ?)
            """, (str(round(self.cash, 2)), datetime.now(timezone.utc).isoformat()))

    def _place_order(self, symbol: str, qty: int,
                     transaction_type: str, order_type: str = "MARKET",
                     price: float = 0.0) -> dict:
        """
        Place an order via Upstox API v3.
        Returns {"success": bool, "order_id": str, "fill_price": float}
        """
        instrument_token = _get_instrument_token(symbol)
        if not instrument_token:
            return {"success": False, "reason": f"No instrument token for {symbol}"}

        order_details = {
            "symbol":           symbol,
            "instrument_token": instrument_token,
            "qty":              qty,
            "transaction_type": transaction_type,
            "order_type":       order_type,
            "price":            price,
            "product":          "D",       # D = Delivery (CNC)
            "validity":         "DAY",
        }

        # DRY-RUN: log but don't send
        if not LIVE_TRADING_ENABLED:
            log.info(f"DRY-RUN order: {transaction_type} {qty} {symbol} "
                     f"@ {'MARKET' if order_type == 'MARKET' else f'₹{price:.2f}'}")
            return {
                "success":    True,
                "order_id":   f"DRYRUN_{symbol}_{int(time.time())}",
                "fill_price": price,
                "dry_run":    True,
            }

        # LIVE: send to Upstox
        try:
            import upstox_client
            client     = _get_upstox_client()
            order_api  = upstox_client.OrderApiV3(client)

            body = upstox_client.PlaceOrderV3Request(
                quantity=qty,
                product="D",
                validity="DAY",
                price=price if order_type == "LIMIT" else 0,
                instrument_token=instrument_token,
                order_type=order_type,
                transaction_type=transaction_type,
                disclosed_quantity=0,
                trigger_price=0,
                is_amo=False,
                slice=True,
            )

            response = order_api.place_order(body, algo_name="QuantSentinel")
            order_id = response.data.order_id if response.data else "UNKNOWN"

            log.info(f"Order placed: {transaction_type} {qty} {symbol} "
                     f"| OrderID: {order_id}")

            # Poll for fill price (Upstox fills market orders quickly)
            fill_price = self._get_fill_price(order_api, order_id, price)

            return {
                "success":    True,
                "order_id":   order_id,
                "fill_price": fill_price,
                "dry_run":    False,
            }

        except Exception as e:
            log.error(f"Order failed for {symbol}: {e}")
            return {"success": False, "reason": str(e)}

    def _get_fill_price(self, order_api, order_id: str,
                         fallback_price: float) -> float:
        """Poll order status to get actual fill price."""
        try:
            for _ in range(5):   # try 5 times with 1s delay
                time.sleep(1)
                orders = order_api.get_order_details(order_id=order_id)
                if orders.data and orders.data.status in ("complete", "filled"):
                    return float(orders.data.average_price or fallback_price)
            return fallback_price
        except Exception:
            return fallback_price

    # =========================================================================
    # PUBLIC INTERFACE (identical to PaperTrader)
    # =========================================================================

    def buy(self, symbol: str, price: float, qty: int,
            stop_loss: float, take_profit: float,
            risk_state: str = "GREEN", reason: str = "Signal") -> dict:
        """Place a live BUY order. Same interface as PaperTrader.buy()"""
        if symbol in self.positions:
            return {"success": False, "reason": f"Already holding {symbol}"}
        if qty < 1:
            return {"success": False, "reason": "Quantity must be >= 1"}

        estimated_cost = qty * price * 1.002   # rough cost estimate
        if estimated_cost > self.cash:
            return {"success": False,
                    "reason": f"Insufficient funds: need ~₹{estimated_cost:,.0f}"}

        result = self._place_order(symbol, qty, "BUY", "MARKET")
        if not result["success"]:
            return result

        fill_price = result["fill_price"] or price
        actual_cost = qty * fill_price
        brokerage   = BROKERAGE_PER_ORDER
        exchange_charge = actual_cost * EXCHANGE_TXN_RATE
        total_cost  = actual_cost + brokerage + exchange_charge

        self.cash -= total_cost

        from execution.paper_trader import PaperPosition
        position = PaperPosition(
            symbol=symbol, qty=qty,
            avg_entry_price=fill_price,
            entry_date=date.today().isoformat(),
            stop_loss=stop_loss, take_profit=take_profit,
            trailing_stop=stop_loss, risk_state=risk_state,
            highest_price=fill_price,
        )
        self.positions[symbol] = position

        # Persist
        with get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO positions
                (symbol, qty, avg_entry_price, stop_loss, take_profit,
                 trailing_stop, entry_date, risk_state_at_entry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, qty, fill_price, stop_loss, take_profit,
                  stop_loss, date.today().isoformat(), risk_state))
        self._save_cash()

        with get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (symbol, action, qty, price, brokerage,
                slippage, reason, risk_state, pnl, created_at)
                VALUES (?, 'BUY', ?, ?, ?, 0, ?, ?, NULL, ?)
            """, (symbol, qty, fill_price, brokerage, reason, risk_state,
                  datetime.now(timezone.utc).isoformat()))

        mode_tag = "" if LIVE_TRADING_ENABLED else "[DRY-RUN] "
        log_trade("BUY", symbol, qty, fill_price, reason)
        log_activity(
            f"{mode_tag}BUY {symbol}: {qty} @ ₹{fill_price:.2f} | "
            f"OrderID={result['order_id']} | Cost=₹{total_cost:,.0f}",
            level="INFO"
        )

        return {
            "success":    True,
            "fill_price": fill_price,
            "total_cost": round(total_cost, 2),
            "order_id":   result["order_id"],
            "reason":     "OK",
        }

    def sell(self, symbol: str, price: float,
             reason: str = "Signal") -> dict:
        """Place a live SELL order. Same interface as PaperTrader.sell()"""
        position = self.positions.get(symbol)
        if position is None:
            return {"success": False, "reason": f"No open position for {symbol}"}

        result = self._place_order(symbol, position.qty, "SELL", "MARKET")
        if not result["success"]:
            return result

        fill_price = result["fill_price"] or price
        proceeds   = fill_price * position.qty

        brokerage      = BROKERAGE_PER_ORDER
        stt            = proceeds * STT_RATE_DELIVERY
        exchange_charge = proceeds * EXCHANGE_TXN_RATE
        net_proceeds   = proceeds - brokerage - stt - exchange_charge

        entry_cost = position.qty * position.avg_entry_price
        pnl        = net_proceeds - entry_cost
        pnl_pct    = pnl / entry_cost if entry_cost > 0 else 0
        holding_days = (
            date.today() - date.fromisoformat(position.entry_date)
        ).days

        self.cash += net_proceeds
        del self.positions[symbol]

        with get_conn() as conn:
            conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        self._save_cash()

        with get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (symbol, action, qty, price, brokerage,
                slippage, reason, risk_state, pnl, created_at)
                VALUES (?, 'SELL', ?, ?, ?, 0, ?, ?, ?, ?)
            """, (symbol, position.qty, fill_price, brokerage, reason,
                  position.risk_state, round(pnl, 2),
                  datetime.now(timezone.utc).isoformat()))

        mode_tag = "" if LIVE_TRADING_ENABLED else "[DRY-RUN] "
        emoji = "✅" if pnl > 0 else "❌"
        log_trade("SELL", symbol, position.qty, fill_price, reason, pnl)
        log_activity(
            f"{mode_tag}SELL {symbol}: {position.qty} @ ₹{fill_price:.2f} | "
            f"PnL=₹{pnl:+,.0f} ({pnl_pct*100:+.1f}%) {emoji} | "
            f"Held {holding_days}d | OrderID={result['order_id']}",
            level="INFO"
        )

        return {
            "success":     True,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct * 100, 2),
            "fill_price":  fill_price,
            "holding_days": holding_days,
            "order_id":    result["order_id"],
            "reason":      "OK",
        }

    def update_trailing_stop(self, symbol: str, current_price: float,
                              risk_state: str = "GREEN"):
        """Delegate to paper trader logic — no Upstox call needed."""
        from execution.paper_trader import PaperTrader
        # Reuse the same trailing stop logic
        dummy = PaperTrader.__new__(PaperTrader)
        dummy.positions = self.positions
        dummy.update_trailing_stop(symbol, current_price, risk_state)

    def get_portfolio_summary(self, current_prices: dict = None) -> dict:
        """Identical to PaperTrader.get_portfolio_summary()"""
        if current_prices is None:
            current_prices = {}

        positions_data = []
        total_market_value   = 0.0
        total_unrealized_pnl = 0.0

        for sym, pos in self.positions.items():
            cp   = current_prices.get(sym, pos.avg_entry_price)
            upnl = (cp - pos.avg_entry_price) * pos.qty
            mv   = pos.qty * cp
            total_market_value   += mv
            total_unrealized_pnl += upnl

            positions_data.append({
                "symbol":         sym,
                "qty":            pos.qty,
                "entry_price":    round(pos.avg_entry_price, 2),
                "current_price":  round(cp, 2),
                "market_value":   round(mv, 2),
                "unrealized_pnl": round(upnl, 2),
                "unrealized_pct": round(upnl / (pos.avg_entry_price * pos.qty) * 100, 2),
                "stop_loss":      round(pos.stop_loss, 2),
                "take_profit":    round(pos.take_profit, 2),
                "trailing_stop":  round(pos.trailing_stop, 2),
                "entry_date":     pos.entry_date,
                "risk_state":     pos.risk_state,
            })

        total_value = self.cash + total_market_value
        total_return_pct = (
            (total_value - self.initial_capital) / self.initial_capital * 100
        )

        return {
            "cash":             round(self.cash, 2),
            "invested":         round(total_market_value, 2),
            "total_value":      round(total_value, 2),
            "initial_capital":  self.initial_capital,
            "total_return_pct": round(total_return_pct, 2),
            "unrealized_pnl":   round(total_unrealized_pnl, 2),
            "open_positions":   len(self.positions),
            "positions":        positions_data,
        }

    def get_realized_pnl(self) -> dict:
        """Same as PaperTrader.get_realized_pnl()"""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT pnl FROM trades WHERE action='SELL' AND pnl IS NOT NULL"
            ).fetchall()
        total  = sum(r["pnl"] for r in rows)
        wins   = sum(1 for r in rows if r["pnl"] > 0)
        n      = len(rows)
        return {
            "realized_pnl":   round(total, 2),
            "total_trades":   n,
            "winning_trades": wins,
            "losing_trades":  n - wins,
            "win_rate":       round(wins / n * 100, 1) if n > 0 else 0.0,
        }

    def get_live_positions(self) -> list[dict]:
        """
        Fetch actual positions from Upstox (for reconciliation).
        Only available in live mode with a valid token.
        """
        if not LIVE_TRADING_ENABLED:
            log.debug("get_live_positions: DRY-RUN — returning local state")
            return list(self.positions.keys())

        try:
            import upstox_client
            client       = _get_upstox_client()
            portfolio_api = upstox_client.PortfolioApi(client)
            holdings     = portfolio_api.get_holdings()
            return [
                {"symbol": h.trading_symbol, "qty": h.quantity,
                 "avg_price": h.average_price}
                for h in (holdings.data or [])
            ]
        except Exception as e:
            log.error(f"Failed to fetch live positions: {e}")
            return []