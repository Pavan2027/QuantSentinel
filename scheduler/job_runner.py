"""
scheduler/job_runner.py
------------------------
APScheduler-based cycle runner.

Every 2 hours during market hours (9:15 AM – 3:00 PM IST, trading days only):
  1. Check control flag (RUNNING / PAUSED / KILLED)
  2. Check market hours — abort if closed
  3. Fetch latest prices + news
  4. Run FinBERT sentiment scoring
  5. Evaluate risk state
  6. Score universe and generate signals
  7. Execute BUY/SELL via paper_trader
  8. Write state to DB for UI
  9. Log everything

The scheduler also checks exits every 30 minutes (more frequent than entries)
to ensure stop losses and trailing stops fire promptly.
"""

import sys
import traceback
from datetime import datetime

import pytz

from config.market_calendar import is_signal_window_open, is_trading_day, get_market_status
from config.settings import CYCLE_INTERVAL_HOURS, MARKET_TIMEZONE
from utils.db import read_control_flag, write_control_flag, log_activity, init_db
from utils.logger import get_logger, log_cycle

# Add at the top of job_runner.py, after imports:
_news_cache: dict[str, list] = {}
_news_cache_time: float = 0.0
NEWS_REFRESH_HOURS = 6   # re-fetch news every 6 hours, not every cycle

log = get_logger("scheduler")
IST = pytz.timezone(MARKET_TIMEZONE)

_cycle_count = 0


def run_cycle(paper_trader, risk_manager):
    """
    Execute one full pipeline cycle.
    Called by the scheduler every 2 hours.
    """
    global _cycle_count
    _cycle_count += 1
    cycle_num = _cycle_count

    # --- Gate 1: Control flag ---
    status = read_control_flag("BOT_STATUS", default="RUNNING")
    if status == "KILLED":
        log.info("Bot killed via UI — stopping scheduler")
        log_activity("⛔ Bot killed via UI control flag", level="WARNING")
        return "KILLED"
    if status == "PAUSED":
        log.info(f"Cycle {cycle_num} skipped — bot paused")
        log_activity(f"⏸️ Cycle {cycle_num} skipped (paused)", level="INFO")
        return "PAUSED"

    # --- Gate 2: Market hours ---
    now = datetime.now(IST)
    '''if not is_signal_window_open(now):
        market_info = get_market_status()
        log.info(f"Cycle {cycle_num} skipped — market closed. "
                 f"Next open: {market_info['next_open']}")
        return "MARKET_CLOSED"'''

    log.info(f"{'='*60}")
    log.info(f"Cycle {cycle_num} starting at {now.strftime('%H:%M:%S IST')}")
    log_activity(f"🔄 Cycle {cycle_num} started", level="INFO")

    try:
        # --- Step 1: Universe ---
        log_activity("Loading stock universe...", level="INFO")
        from strategy.universe import get_raw_universe
        risk_state = risk_manager.current_state
        symbols    = get_raw_universe(risk_state)

        # --- Step 2: Price data ---
        log_activity(f"Fetching price data for {len(symbols)} stocks...", level="INFO")
        from data.price_provider import get_price_data_batch
        price_data = get_price_data_batch(symbols, lookback_days=90)
        if not price_data:
            log.error("No price data fetched — aborting cycle")
            return "NO_DATA"

        # --- Step 3: Technical signals ---
        log_activity("Computing technical indicators...", level="INFO")
        from features.technicals import compute_all_signals
        from features.preprocessing import clean_ohlcv
        from features.corporate_actions import is_data_safe
        stock_signals = {}
        for sym, df in price_data.items():
            cleaned = clean_ohlcv(df, sym)
            if cleaned is not None and is_data_safe(cleaned, sym):
                signals = compute_all_signals(cleaned, sym)
                if signals:
                    stock_signals[sym] = signals

        log.info(f"Signals computed: {len(stock_signals)}/{len(price_data)} stocks")

        # --- Step 4: News + Sentiment ---
        import time
        global _news_cache, _news_cache_time

        now_ts = time.time()
        cache_age_hours = (now_ts - _news_cache_time) / 3600

        if not _news_cache or cache_age_hours >= NEWS_REFRESH_HOURS:
            log_activity(f"Fetching fresh news (cache age: {cache_age_hours:.1f}h)...", level="INFO")
            try:
                import concurrent.futures
                from data.news_provider import get_news_for_stock
                from sentiment.finbert_model import FinBERTModel, is_finbert_available
                from sentiment.aggregator import aggregate_universe_sentiment, get_sentiment_scores_only

                if is_finbert_available():
                    stock_news = {}
                    top_symbols = list(stock_signals.keys())[:30]

                    def _fetch_one(sym):
                        try:
                            return sym, get_news_for_stock(sym)
                        except Exception:
                            return sym, []

                    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                        for sym, news in ex.map(_fetch_one, top_symbols):
                            stock_news[sym] = news

                    finbert = FinBERTModel()
                    universe_sentiment = aggregate_universe_sentiment(finbert, stock_news)
                    _news_cache = get_sentiment_scores_only(universe_sentiment)
                    _news_cache_time = now_ts
                    log_activity(f"News cache refreshed: {len(_news_cache)} stocks scored", level="INFO")
                else:
                    log_activity("FinBERT not available — using neutral sentiment", level="INFO")
            except Exception as e:
                log.warning(f"News fetch failed: {e} — using cached/neutral scores")
        else:
            log_activity(f"Using cached sentiment (age: {cache_age_hours:.1f}h)", level="INFO")

        sentiment_scores = _news_cache

        # --- Step 5: Risk state ---
        log_activity("Evaluating risk state...", level="INFO")
        current_prices = {
            sym: float(df["Close"].iloc[-1])
            for sym, df in price_data.items()
            if not df.empty
        }
        positions_dict = {
            sym: {"qty": pos.qty, "avg_entry_price": pos.avg_entry_price}
            for sym, pos in paper_trader.positions.items()
        }
        portfolio_summary = paper_trader.get_portfolio_summary(current_prices)
        risk_state = risk_manager.evaluate(
            portfolio_value=portfolio_summary["total_value"],
            cash=paper_trader.cash,
            open_positions=positions_dict,
            current_prices=current_prices,
        )

        # --- Step 6: Check exits ---
        log_activity("Checking exit conditions...", level="INFO")
        exits_fired = _check_exits(paper_trader, current_prices, risk_state)

        # --- Step 7: Score and generate entries ---
        log_activity("Scoring stocks and generating entry signals...", level="INFO")
        from strategy.scoring import score_all_stocks, get_top_picks
        from strategy.signal_engine import generate_entry_signal, BUY
        from strategy.scoring import compute_exit_levels
        from risk.exposure_limits import validate_new_buy

        ranked = score_all_stocks(stock_signals, risk_state, sentiment_scores)
        picks  = get_top_picks(ranked, risk_state)
        entries_fired = 0

        for pick in picks:
            sym     = pick["symbol"]
            score   = pick["score"]
            signals = pick["signals"]

            if sym in paper_trader.positions:
                continue

            signal = generate_entry_signal(
                sym, score, risk_state,
                has_open_position=sym in paper_trader.positions,
                signals=signals,
            )
            if signal != BUY:
                continue

            price = current_prices.get(sym)
            if not price:
                continue

            # Exposure checks
            atr = signals.get("atr_val", price * 0.02)
            from risk.exposure_limits import MAX_POSITIONS
            size_pct  = {"GREEN": 0.15, "YELLOW": 0.08, "RED": 0.04}.get(risk_state, 0.12)
            trade_val = paper_trader.cash * size_pct
            qty       = max(1, int(trade_val / price))
            actual_val = qty * price

            checks = validate_new_buy(
                symbol=sym, trade_value=actual_val,
                cash=paper_trader.cash,
                portfolio_value=portfolio_summary["total_value"],
                initial_capital=paper_trader.initial_capital,
                open_positions=positions_dict,
                current_prices=current_prices,
                risk_state=risk_state,
            )
            if not checks["allowed"]:
                log.debug(f"BUY blocked for {sym}: {checks['failures']}")
                continue

            exits = compute_exit_levels(price, atr, risk_state)
            result = paper_trader.buy(
                sym, price, qty,
                stop_loss=exits["stop_loss"],
                take_profit=exits["take_profit"],
                risk_state=risk_state,
                reason=f"Score={score:.3f}",
            )
            if result["success"]:
                entries_fired += 1
                log_activity(
                    f"✅ BUY {sym}: {qty} @ ₹{price:.2f} | "
                    f"score={score:.3f} | state={risk_state}",
                    level="INFO"
                )

        # --- Step 8: Log cycle summary ---
        log_cycle(cycle_num, risk_state, len(stock_signals), entries_fired)
        log_activity(
            f"✅ Cycle {cycle_num} complete | "
            f"State={risk_state} | "
            f"Entries={entries_fired} | Exits={exits_fired} | "
            f"Portfolio=₹{portfolio_summary['total_value']:,.0f}",
            level="INFO"
        )
        return "OK"

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"Cycle {cycle_num} failed: {e}\n{tb}")
        log_activity(f"❌ Cycle {cycle_num} ERROR: {str(e)[:100]}", level="ERROR")
        return "ERROR"


def _check_exits(paper_trader, current_prices: dict, risk_state: str) -> int:
    """Check all open positions for exit conditions. Returns number of exits fired."""
    from strategy.signal_engine import generate_exit_signal, SELL, Position
    from datetime import date

    exits = 0
    for sym in list(paper_trader.positions.keys()):
        pos = paper_trader.positions.get(sym)
        if not pos:
            continue

        price = current_prices.get(sym)
        if not price:
            continue

        # Update trailing stop first
        paper_trader.update_trailing_stop(sym, price, risk_state)

        signal_pos = Position(
            symbol=sym,
            qty=pos.qty,
            avg_entry_price=pos.avg_entry_price,
            entry_date=date.fromisoformat(pos.entry_date),
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            trailing_stop=pos.trailing_stop,
            risk_state_at_entry=pos.risk_state,
            highest_price_seen=pos.highest_price,
        )

        signal, reason = generate_exit_signal(
            signal_pos, price, date.today(), risk_state
        )

        if signal == SELL:
            result = paper_trader.sell(sym, price, reason=reason)
            if result["success"]:
                exits += 1
                log_activity(
                    f"📤 SELL {sym} @ ₹{price:.2f} | "
                    f"PnL=₹{result['pnl']:+,.0f} ({result['pnl_pct']:+.1f}%) | "
                    f"{reason}",
                    level="INFO"
                )
    return exits


def start_scheduler(paper_trader, risk_manager):
    """
    Start the APScheduler with two jobs:
      - Full cycle every 2 hours (entries + exits)
      - Exit-only check every 30 minutes (faster stop loss response)
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.error("APScheduler not installed. Run: pip install APScheduler")
        raise

    write_control_flag("BOT_STATUS", "RUNNING")
    log_activity("🚀 Bot started", level="INFO")

    scheduler = BlockingScheduler(timezone=IST)

    # Full cycle: every 2 hours
    scheduler.add_job(
        func=lambda: run_cycle(paper_trader, risk_manager),
        trigger=IntervalTrigger(hours=CYCLE_INTERVAL_HOURS),
        id="full_cycle",
        name="Full pipeline cycle",
        max_instances=1,
        coalesce=True,
    )

    # Exit-only check: every 30 minutes
    scheduler.add_job(
        func=lambda: _check_exits(
            paper_trader,
            _get_current_prices(paper_trader),
            risk_manager.current_state,
        ),
        trigger=IntervalTrigger(minutes=10),
        id="exit_check",
        name="Exit condition check",
        max_instances=1,
        coalesce=True,
    )

    log.info("Scheduler started. Running full cycle immediately...")

    # Run one cycle immediately on start
    run_cycle(paper_trader, risk_manager)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        write_control_flag("BOT_STATUS", "STOPPED")
        log_activity("🛑 Bot stopped", level="INFO")
        log.info("Scheduler stopped")


def _get_current_prices(paper_trader) -> dict:
    """Fetch latest prices for all open positions."""
    from data.price_provider import get_latest_price
    return {
        sym: get_latest_price(sym) or pos.avg_entry_price
        for sym, pos in paper_trader.positions.items()
    }