"""
utils/notifier.py
------------------
Sends alerts for important bot events via Telegram.

Events that trigger alerts:
  - Risk state change (GREEN → YELLOW → RED)
  - Trade executed (BUY or SELL with PnL)
  - Stop loss hit
  - Take profit hit
  - Bot error / crash
  - Daily summary (portfolio snapshot)

Setup:
  1. Create a Telegram bot via @BotFather
  2. Get your chat ID by messaging the bot and visiting:
     https://api.telegram.org/bot{TOKEN}/getUpdates
  3. Add to .env:
     TELEGRAM_BOT_TOKEN=your_bot_token
     TELEGRAM_CHAT_ID=your_chat_id
"""

import os
import requests
from datetime import datetime
from utils.logger import get_logger

log = get_logger("notifier")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(message: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message. Returns True if successful."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": parse_mode,
            },
            timeout=5,
        )
        if resp.status_code == 200:
            return True
        log.warning(f"Telegram send failed: {resp.status_code} — {resp.text[:100]}")
        return False
    except Exception as e:
        log.warning(f"Telegram error: {e}")
        return False


# =============================================================================
# PUBLIC NOTIFICATION FUNCTIONS
# =============================================================================

def notify_trade_buy(symbol: str, qty: int, price: float,
                      stop_loss: float, take_profit: float,
                      score: float, risk_state: str):
    msg = (
        f"📈 <b>BUY — {symbol}</b>\n"
        f"Qty: {qty} @ ₹{price:,.2f}\n"
        f"Stop Loss:   ₹{stop_loss:,.2f}\n"
        f"Take Profit: ₹{take_profit:,.2f}\n"
        f"Score: {score:.3f} | State: {risk_state}"
    )
    _send(msg)


def notify_trade_sell(symbol: str, qty: int, price: float,
                       pnl: float, pnl_pct: float,
                       reason: str, holding_days: int):
    emoji = "✅" if pnl > 0 else "❌"
    msg = (
        f"{emoji} <b>SELL — {symbol}</b>\n"
        f"Qty: {qty} @ ₹{price:,.2f}\n"
        f"PnL: ₹{pnl:+,.0f} ({pnl_pct:+.1f}%)\n"
        f"Held: {holding_days} days\n"
        f"Reason: {reason}"
    )
    _send(msg)


def notify_risk_state_change(old_state: str, new_state: str,
                              drawdown_pct: float, cash: float,
                              losers: int):
    icons = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
    msg = (
        f"{icons.get(new_state, '⚪')} <b>Risk: {old_state} → {new_state}</b>\n"
        f"Drawdown: {drawdown_pct:.1f}%\n"
        f"Cash: ₹{cash:,.0f}\n"
        f"Losing positions: {losers}"
    )
    _send(msg)


def notify_daily_summary(portfolio_value: float, cash: float,
                          total_return_pct: float, open_positions: int,
                          realized_pnl: float, win_rate: float,
                          risk_state: str):
    icons = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
    trend = "📈" if total_return_pct >= 0 else "📉"
    msg = (
        f"{trend} <b>Daily Summary — QuantSentinel</b>\n"
        f"Portfolio: ₹{portfolio_value:,.0f}\n"
        f"Return: {total_return_pct:+.2f}%\n"
        f"Cash: ₹{cash:,.0f}\n"
        f"Open positions: {open_positions}\n"
        f"Realized PnL: ₹{realized_pnl:+,.0f}\n"
        f"Win Rate: {win_rate:.1f}%\n"
        f"Risk State: {icons.get(risk_state, '⚪')} {risk_state}"
    )
    _send(msg)


def notify_error(error_msg: str, cycle_num: int = None):
    cycle_info = f" (Cycle {cycle_num})" if cycle_num else ""
    msg = (
        f"❌ <b>Bot Error{cycle_info}</b>\n"
        f"<code>{error_msg[:300]}</code>"
    )
    _send(msg)


def notify_bot_started(mode: str, capital: float):
    msg = (
        f"🚀 <b>QuantSentinel Started</b>\n"
        f"Mode: {mode}\n"
        f"Capital: ₹{capital:,.0f}\n"
        f"Time: {datetime.now().strftime('%H:%M IST')}"
    )
    _send(msg)


def notify_market_closed():
    """Send once when market closes for the day."""
    _send("🔔 Market closed. Bot paused until 9:15 AM IST tomorrow.")


def test_notification():
    """Send a test message to verify Telegram is configured correctly."""
    success = _send(
        "✅ <b>QuantSentinel</b> — Telegram notifications working!"
    )
    if success:
        print("✅ Test notification sent successfully")
    else:
        print("❌ Notification failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    return success


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    test_notification()