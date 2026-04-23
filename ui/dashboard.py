"""
ui/dashboard.py
----------------
QuantSentinel monitoring dashboard.

Run standalone:  streamlit run ui/dashboard.py
Run via main.py: python main.py --ui-only

Layout:
  - Header: bot status + kill/pause/resume controls
  - Row 1: Risk state panel + Portfolio summary cards
  - Row 2: Open positions table
  - Row 3: Signal feed (last 20 signals)
  - Row 4: Activity log (last 50 events)
  - Auto-refreshes every 30 seconds
"""

import sys
from pathlib import Path

# Ensure project root is on path when running standalone
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
import pandas as pd
from datetime import datetime
import pytz

from utils.db import (
    read_control_flag, write_control_flag,
    get_recent_activity, get_recent_signals, init_db,
)
from config.settings import PAPER_CAPITAL_INR

init_db()
IST = pytz.timezone("Asia/Kolkata")

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="QuantSentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =============================================================================
# CUSTOM CSS
# =============================================================================
st.markdown("""
<style>
  .main { background-color: #0f172a; }
  .stApp { background-color: #0f172a; color: #e2e8f0; }

  .metric-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 4px 0;
  }
  .metric-label { font-size: 0.72rem; color: #64748b; text-transform: uppercase;
                  letter-spacing: .06em; margin-bottom: 2px; }
  .metric-value { font-size: 1.5rem; font-weight: 700; }
  .pos  { color: #22c55e; }
  .neg  { color: #ef4444; }
  .neu  { color: #e2e8f0; }
  .warn { color: #f59e0b; }

  .risk-badge {
    display: inline-block; padding: 6px 18px;
    border-radius: 999px; font-weight: 700; font-size: 1rem;
  }
  .risk-GREEN  { background: #22c55e22; color: #22c55e; border: 1px solid #22c55e55; }
  .risk-YELLOW { background: #f59e0b22; color: #f59e0b; border: 1px solid #f59e0b55; }
  .risk-RED    { background: #ef444422; color: #ef4444; border: 1px solid #ef444455; }

  .status-dot {
    display: inline-block; width: 10px; height: 10px;
    border-radius: 50%; margin-right: 6px;
  }
  .dot-running { background: #22c55e; animation: pulse 2s infinite; }
  .dot-paused  { background: #f59e0b; }
  .dot-killed  { background: #ef4444; }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.4; }
  }

  .section-header {
    font-size: 0.8rem; font-weight: 600; color: #64748b;
    text-transform: uppercase; letter-spacing: .08em;
    margin: 20px 0 8px; border-bottom: 1px solid #1e293b;
    padding-bottom: 6px;
  }

  .log-entry { font-size: 0.78rem; padding: 4px 0;
               border-bottom: 1px solid #1e293b22; color: #94a3b8; }
  .log-entry .ts { color: #475569; margin-right: 8px; }
  .log-INFO    { color: #94a3b8; }
  .log-WARNING { color: #f59e0b; }
  .log-ERROR   { color: #ef4444; }

  div[data-testid="stMetric"] label { color: #64748b !important; }
  .stButton button {
    border-radius: 8px !important; font-weight: 600 !important;
    border: none !important;
  }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# HELPERS
# =============================================================================

def _card(label: str, value: str, css_class: str = "neu") -> str:
    return f"""
    <div class="metric-card">
      <div class="metric-label">{label}</div>
      <div class="metric-value {css_class}">{value}</div>
    </div>"""


def _risk_badge(state: str) -> str:
    icons = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
    return (f'<span class="risk-badge risk-{state}">'
            f'{icons.get(state, "⚪")} {state}</span>')


def _fmt_inr(val: float) -> str:
    return f"₹{val:,.0f}"


def _fmt_pct(val: float, plus: bool = True) -> str:
    sign = "+" if plus and val > 0 else ""
    return f"{sign}{val:.2f}%"


def _pnl_class(val: float) -> str:
    return "pos" if val > 0 else ("neg" if val < 0 else "neu")


def _utc_to_ist(utc_str: str) -> str:
    if not utc_str:
        return "—"
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return utc_str[:16].replace("T", " ")


def _get_portfolio():
    try:
        from execution.paper_trader import PaperTrader
        import yfinance as yf
        trader = PaperTrader(initial_capital=PAPER_CAPITAL_INR)
        
        # Fetch live prices for all open positions in one batch call
        if trader.positions:
            symbols = [f"{sym}.NS" for sym in trader.positions.keys()]
            try:
                tickers = yf.download(
                    symbols, period="1d", interval="5m",
                    progress=False, auto_adjust=True
                )
                prices = {}
                for sym in trader.positions.keys():
                    try:
                        col = f"{sym}.NS"
                        if ("Close", col) in tickers.columns:
                            val = tickers["Close"][col].dropna().iloc[-1]
                        else:
                            val = tickers["Close"].dropna().iloc[-1]
                        prices[sym] = float(val)
                    except Exception:
                        prices[sym] = trader.positions[sym].avg_entry_price
            except Exception:
                prices = {
                    sym: trader.positions[sym].avg_entry_price
                    for sym in trader.positions
                }
        else:
            prices = {}
            
        return trader.get_portfolio_summary(prices), trader.get_realized_pnl()
    except Exception as e:
        return None, None


# =============================================================================
# HEADER — Status + Controls
# =============================================================================

def render_header():
    bot_status = read_control_flag("BOT_STATUS", "STOPPED")
    risk_state = read_control_flag("RISK_STATE", "GREEN")
    now_ist    = datetime.now(IST).strftime("%d %b %Y  %H:%M:%S IST")

    col_title, col_status, col_controls = st.columns([3, 2, 3])

    with col_title:
        st.markdown("## 📈 QuantSentinel")
        st.markdown(f'<span style="color:#475569;font-size:0.8rem">{now_ist}</span>',
                    unsafe_allow_html=True)

    with col_status:
        dot_cls = {
            "RUNNING": "dot-running",
            "PAUSED":  "dot-paused",
            "KILLED":  "dot-killed",
            "STOPPED": "dot-killed",
        }.get(bot_status, "dot-killed")

        st.markdown(f"""
        <div style="margin-top:12px">
          <span class="status-dot {dot_cls}"></span>
          <span style="font-weight:600;font-size:1rem">{bot_status}</span>
          &nbsp;&nbsp;
          {_risk_badge(risk_state)}
        </div>
        """, unsafe_allow_html=True)

    with col_controls:
        st.markdown('<div style="margin-top:8px"></div>', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("▶ Resume", width='stretch'):
                write_control_flag("BOT_STATUS", "RUNNING")
                st.rerun()
        with c2:
            if st.button("⏸ Pause", width='stretch'):
                write_control_flag("BOT_STATUS", "PAUSED")
                st.rerun()
        with c3:
            if st.button("⛔ Kill", width='stretch', type="primary"):
                write_control_flag("BOT_STATUS", "KILLED")
                st.rerun()
        with c4:
            if st.button("🔄 Refresh", width='stretch'):
                st.rerun()

    st.divider()


# =============================================================================
# RISK + PORTFOLIO SUMMARY
# =============================================================================

def render_risk_and_portfolio(portfolio, realized):
    risk_state = read_control_flag("RISK_STATE", "GREEN")

    col_risk, col_portfolio = st.columns([1, 3])

    with col_risk:
        st.markdown('<div class="section-header">Risk State</div>',
                    unsafe_allow_html=True)

        # Drawdown meter
        try:
            from execution.paper_trader import PaperTrader
            trader = PaperTrader(initial_capital=PAPER_CAPITAL_INR)
            total_val = portfolio["total_value"] if portfolio else PAPER_CAPITAL_INR
            drawdown_pct = max(0, (PAPER_CAPITAL_INR - total_val) / PAPER_CAPITAL_INR * 100)
        except Exception:
            drawdown_pct = 0.0

        st.markdown(_risk_badge(risk_state), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        dd_color = "#22c55e" if drawdown_pct < 5 else ("#f59e0b" if drawdown_pct < 12 else "#ef4444")
        st.markdown(f"""
        <div style="margin-top:8px">
          <div style="font-size:0.72rem;color:#64748b;margin-bottom:4px">
            DRAWDOWN FROM PEAK
          </div>
          <div style="background:#1e293b;border-radius:6px;overflow:hidden;height:10px;margin-bottom:4px">
            <div style="background:{dd_color};width:{min(drawdown_pct*5,100):.0f}%;
                        height:100%;border-radius:6px;transition:width 0.3s">
            </div>
          </div>
          <div style="font-size:1.1rem;font-weight:700;color:{dd_color}">
            -{drawdown_pct:.1f}%
          </div>
        </div>
        """, unsafe_allow_html=True)

        thresholds_html = """
        <div style="margin-top:14px;font-size:0.72rem;color:#475569">
          <div>🟢 GREEN  &lt; 5% DD</div>
          <div>🟡 YELLOW 5–12% DD</div>
          <div>🔴 RED    &gt; 12% DD</div>
        </div>"""
        st.markdown(thresholds_html, unsafe_allow_html=True)

    with col_portfolio:
        st.markdown('<div class="section-header">Portfolio</div>',
                    unsafe_allow_html=True)

        if portfolio:
            ret_pct = portfolio["total_return_pct"]
            upnl    = portfolio["unrealized_pnl"]
            rpnl    = realized["realized_pnl"] if realized else 0
            win_rt  = realized["win_rate"] if realized else 0
            trades  = realized["total_trades"] if realized else 0

            c1, c2, c3, c4, c5, c6 = st.columns(6)
            cards = [
                (c1, "Total Value",    _fmt_inr(portfolio["total_value"]),  "neu"),
                (c2, "Cash",          _fmt_inr(portfolio["cash"]),          "neu"),
                (c3, "Invested",      _fmt_inr(portfolio["invested"]),      "neu"),
                (c4, "Total Return",  _fmt_pct(ret_pct),  _pnl_class(ret_pct)),
                (c5, "Unrealized PnL",_fmt_inr(upnl),    _pnl_class(upnl)),
                (c6, "Realized PnL",  _fmt_inr(rpnl),    _pnl_class(rpnl)),
            ]
            for col, lbl, val, cls in cards:
                with col:
                    st.markdown(_card(lbl, val, cls), unsafe_allow_html=True)

            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown(_card("Open Positions",
                                  str(portfolio["open_positions"]), "neu"),
                            unsafe_allow_html=True)
            with c2:
                st.markdown(_card("Total Trades", str(trades), "neu"),
                            unsafe_allow_html=True)
            with c3:
                st.markdown(_card("Win Rate",
                                  f"{win_rt:.1f}%",
                                  "pos" if win_rt >= 50 else "neg"),
                            unsafe_allow_html=True)
        else:
            st.info("No portfolio data yet. Start the bot to begin paper trading.")


# =============================================================================
# OPEN POSITIONS TABLE
# =============================================================================

def render_positions(portfolio):
    st.markdown('<div class="section-header">Open Positions</div>',
                unsafe_allow_html=True)

    if not portfolio or not portfolio.get("positions"):
        st.markdown('<span style="color:#475569;font-size:0.85rem">No open positions</span>',
                    unsafe_allow_html=True)
        return

    rows = []
    for p in portfolio["positions"]:
        pnl_cls = "🟢" if p["unrealized_pnl"] > 0 else "🔴"
        rows.append({
            "Symbol":     p["symbol"],
            "Qty":        p["qty"],
            "Entry ₹":    f"₹{p['entry_price']:,.2f}",
            "Current ₹":  f"₹{p['current_price']:,.2f}",
            "Mkt Value":  f"₹{p['market_value']:,.0f}",
            "PnL ₹":      f"{pnl_cls} ₹{p['unrealized_pnl']:+,.0f}",
            "PnL %":      f"{p['unrealized_pct']:+.2f}%",
            "Stop Loss":  f"₹{p['stop_loss']:,.2f}",
            "Take Profit":f"₹{p['take_profit']:,.2f}",
            "Trail Stop": f"₹{p['trailing_stop']:,.2f}",
            "Entry Date": p["entry_date"],
            "State":      p["risk_state"],
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, width='stretch', hide_index=True)


# =============================================================================
# SIGNAL FEED
# =============================================================================

def render_signal_feed():
    st.markdown('<div class="section-header">Recent Signals</div>',
                unsafe_allow_html=True)

    signals = get_recent_signals(limit=20)
    if not signals:
        st.markdown('<span style="color:#475569;font-size:0.85rem">No signals generated yet</span>',
                    unsafe_allow_html=True)
        return

    rows = []
    for s in signals:
        ts = _utc_to_ist(s.get("created_at", ""))
        sig_icon = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "⚪ HOLD"}.get(
            s.get("signal", "HOLD"), s.get("signal", "HOLD")
        )
        rows.append({
            "Time":      ts,
            "Symbol":    s.get("symbol", ""),
            "Signal":    sig_icon,
            "Score":     f"{s.get('score', 0):.3f}" if s.get("score") else "—",
            "Sentiment": f"{s.get('sentiment_score', 0):.3f}" if s.get("sentiment_score") else "—",
            "Momentum":  f"{s.get('momentum_score', 0):.3f}" if s.get("momentum_score") else "—",
            "RSI":       f"{s.get('rsi_score', 0):.3f}" if s.get("rsi_score") else "—",
            "Risk State":s.get("risk_state", "—"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, width='stretch', hide_index=True)


# =============================================================================
# ACTIVITY LOG
# =============================================================================

def render_activity_log():
    st.markdown('<div class="section-header">Activity Log</div>',
                unsafe_allow_html=True)

    activities = get_recent_activity(limit=50)
    if not activities:
        st.markdown('<span style="color:#475569;font-size:0.85rem">No activity yet</span>',
                    unsafe_allow_html=True)
        return

    log_html = ""
    for a in activities:
        ts  = _utc_to_ist(a.get("created_at", ""))
        msg = a.get("message", "")
        lvl = a.get("level", "INFO")
        log_html += (
            f'<div class="log-entry log-{lvl}">'
            f'<span class="ts">{ts}</span>{msg}</div>'
        )

    st.markdown(
        f'<div style="background:#0f172a;border:1px solid #1e293b;'
        f'border-radius:8px;padding:12px;max-height:300px;overflow-y:auto">'
        f'{log_html}</div>',
        unsafe_allow_html=True,
    )


# =============================================================================
# SIDEBAR — Settings
# =============================================================================

def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ Settings")
        refresh = st.slider("Auto-refresh (seconds)", 15, 120, 30, 5)

        st.markdown("---")
        st.markdown("### 🔧 Manual Controls")

        if st.button("Run One Cycle Now", width='stretch'):
            try:
                from execution.paper_trader import PaperTrader
                from risk.risk_manager import RiskManager
                from scheduler.job_runner import run_cycle
                trader = PaperTrader(initial_capital=PAPER_CAPITAL_INR)
                rm     = RiskManager(initial_capital=PAPER_CAPITAL_INR)
                with st.spinner("Running cycle..."):
                    result = run_cycle(trader, rm)
                st.success(f"Cycle result: {result}")
            except Exception as e:
                st.error(f"Cycle failed: {e}")

        if st.button("Request Upstox Token", width='stretch'):
            try:
                from execution.upstox_auth import request_token_v3, is_token_valid
                if is_token_valid():
                    st.success("✅ Token is already valid for today!")
                else:
                    with st.spinner("Ping sent to Upstox! Please check your WhatsApp/Phone to approve..."):
                        token = request_token_v3()
                        st.success(f"✅ Token secured! [{token[:10]}...]")
            except TimeoutError:
                st.error("❌ Token request timed out after 10 minutes.")
            except Exception as e:
                st.error(f"❌ Error: {e}")

        st.markdown("---")
        st.markdown("### ⚠️ Danger Zone")
        if st.checkbox("Enable reset"):
            if st.button("Reset Paper Trader", type="primary",
                         width='stretch'):
                try:
                    from execution.paper_trader import PaperTrader
                    trader = PaperTrader(initial_capital=PAPER_CAPITAL_INR)
                    trader.reset(confirm=True)
                    st.success("Paper trader reset to initial capital")
                    st.rerun()
                except Exception as e:
                    st.error(f"Reset failed: {e}")

        st.markdown("---")
        st.markdown("### 📊 Quick Stats")
        from config.market_calendar import get_market_status
        ms = get_market_status()
        status_str = "🟢 Open" if ms["is_market_open"] else "🔴 Closed"
        st.markdown(f"**Market:** {status_str}")
        st.markdown(f"**Next open:** {ms['next_open'][:16].replace('T', ' ')}")
        st.markdown(f"**Signal window:** {'✅' if ms['is_signal_window'] else '❌'}")

        return refresh


# =============================================================================
# MAIN
# =============================================================================

@st.fragment(run_every="30s")
def render_live_data():
    portfolio, realized = _get_portfolio()

    render_risk_and_portfolio(portfolio, realized)
    st.markdown("<br>", unsafe_allow_html=True)

    render_positions(portfolio)
    st.markdown("<br>", unsafe_allow_html=True)

    col_signals, col_log = st.columns([1, 1])
    with col_signals:
        render_signal_feed()
    with col_log:
        render_activity_log()
        
    st.markdown(
        f'<div style="text-align:right;color:#334155;font-size:0.7rem;margin-top:20px">'
        f'Live data updating automatically | QuantSentinel Paper Trading</div>',
        unsafe_allow_html=True,
    )

def main():
    render_sidebar()
    render_header()
    render_live_data()

if __name__ == "__main__":
    main()