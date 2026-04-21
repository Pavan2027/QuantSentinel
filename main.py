"""
main.py
--------
Entry point for QuantSentinel.

Trading mode is controlled by .env:
  LIVE_TRADING=false  → Paper trading (default, safe)
  LIVE_TRADING=true   → Live trading via Upstox (real money)

Usage:
  python main.py              # start scheduler + UI
  python main.py --bot-only   # scheduler only
  python main.py --ui-only    # UI only
  python main.py --cycle-now  # one cycle then exit
  python main.py --auth       # run Upstox OAuth2 flow only
"""

import argparse
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

from utils.db import init_db, write_control_flag
from utils.logger import get_logger

log = get_logger("main")

LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"


def _get_trader():
    """Return paper or live trader based on LIVE_TRADING env var."""
    from config.settings import PAPER_CAPITAL_INR
    if LIVE_TRADING:
        log.warning("⚠️  LIVE TRADING MODE — real money at risk")
        from execution.upstox_client import UpstoxClient
        return UpstoxClient(initial_capital=PAPER_CAPITAL_INR)
    else:
        from execution.paper_trader import PaperTrader
        return PaperTrader(initial_capital=PAPER_CAPITAL_INR)


def run_bot():
    from config.settings import PAPER_CAPITAL_INR
    from risk.risk_manager import RiskManager
    from scheduler.job_runner import start_scheduler

    log.info(f"Starting QuantSentinel ({'LIVE' if LIVE_TRADING else 'PAPER'} mode)...")
    init_db()
    write_control_flag("BOT_STATUS", "RUNNING")
    write_control_flag("TRADING_MODE", "LIVE" if LIVE_TRADING else "PAPER")

    if LIVE_TRADING:
        from execution.upstox_auth import get_valid_token
        log.info("Validating Upstox token...")
        get_valid_token()

    trader = _get_trader()
    rm     = RiskManager(initial_capital=PAPER_CAPITAL_INR)
    start_scheduler(trader, rm)


def run_ui():
    ui_path = Path(__file__).parent / "ui" / "dashboard.py"
    if not ui_path.exists():
        log.error(f"UI not found at {ui_path}")
        return
    log.info("Starting Streamlit dashboard on http://localhost:8501")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(ui_path),
         "--server.headless", "true"],
        check=False,
    )


def run_single_cycle():
    from config.settings import PAPER_CAPITAL_INR
    from risk.risk_manager import RiskManager
    from scheduler.job_runner import run_cycle

    init_db()
    write_control_flag("BOT_STATUS", "RUNNING")

    trader = _get_trader()
    rm     = RiskManager(initial_capital=PAPER_CAPITAL_INR)

    log.info(f"Running single cycle ({'LIVE' if LIVE_TRADING else 'PAPER'} mode)...")
    result = run_cycle(trader, rm)
    log.info(f"Cycle result: {result}")

    summary = trader.get_portfolio_summary({})
    mode    = "LIVE" if LIVE_TRADING else "PAPER"
    print(f"\n[{mode}] Portfolio after cycle:")
    print(f"  Cash:         ₹{summary['cash']:,.0f}")
    print(f"  Invested:     ₹{summary['invested']:,.0f}")
    print(f"  Total value:  ₹{summary['total_value']:,.0f}")
    print(f"  Return:       {summary['total_return_pct']:+.2f}%")
    print(f"  Positions:    {summary['open_positions']}")
    for pos in summary["positions"]:
        print(f"    {pos['symbol']:12s} {pos['qty']}x @ "
              f"₹{pos['entry_price']:.2f} | "
              f"PnL: {pos['unrealized_pct']:+.1f}%")


def run_auth_only():
    """Run Upstox OAuth2 flow standalone."""
    from execution.upstox_auth import run_auth_flow
    run_auth_flow()
    print("\nToken valid for today. Bot is ready to trade live.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuantSentinel AI Trading Bot")
    parser.add_argument("--bot-only",  action="store_true")
    parser.add_argument("--ui-only",   action="store_true")
    parser.add_argument("--cycle-now", action="store_true")
    parser.add_argument("--auth",      action="store_true",
                        help="Run Upstox OAuth2 authentication only")
    args = parser.parse_args()

    init_db()

    if args.auth:
        run_auth_only()
    elif args.cycle_now:
        run_single_cycle()
    elif args.bot_only:
        run_bot()
    elif args.ui_only:
        run_ui()
    else:
        bot_proc = multiprocessing.Process(target=run_bot, name="Bot")
        ui_proc  = multiprocessing.Process(target=run_ui,  name="UI")

        bot_proc.start()
        time.sleep(2)
        ui_proc.start()

        log.info("Both processes started. Press Ctrl+C to stop.")
        try:
            bot_proc.join()
            ui_proc.join()
        except KeyboardInterrupt:
            write_control_flag("BOT_STATUS", "KILLED")
            log.info("Shutting down...")
            bot_proc.terminate()
            ui_proc.terminate()