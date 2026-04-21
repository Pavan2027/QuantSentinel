"""
backtest/report_generator.py
-----------------------------
Generates HTML and CSV reports from backtest results.

HTML report contains:
  - Strategy summary card (return, Sharpe, drawdown, go/no-go verdict)
  - Equity curve (ASCII sparkline inline, proper chart via inline JS)
  - Trade log table (all completed trades)
  - Per-symbol performance breakdown
  - Configuration used
"""

import csv
import json
from datetime import datetime
from pathlib import Path

from utils.logger import get_logger

log = get_logger("report_generator")


def generate_html_report(results: dict, output_path: str = None) -> str:
    """
    Generate a self-contained HTML backtest report.

    Args:
        results:     Output dict from BacktestEngine.run()
        output_path: File path to write HTML. If None, uses reports/backtest_TIMESTAMP.html

    Returns:
        Path to the generated HTML file
    """
    metrics = results.get("metrics", {})
    trades  = results.get("trades", [])
    equity  = results.get("equity_curve", [])
    dates   = results.get("daily_dates", [])
    cfg     = results.get("config", {})

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("reports")
        out_dir.mkdir(exist_ok=True)
        output_path = str(out_dir / f"backtest_{ts}.html")

    verdict       = metrics.get("go_nogo", "UNKNOWN")
    verdict_color = {"GO": "#22c55e", "BORDERLINE": "#f59e0b", "NO-GO": "#ef4444"}.get(verdict, "#6b7280")

    equity_json = json.dumps([round(v, 2) for v in equity])
    dates_json  = json.dumps([str(d) for d in dates])

    # Per-symbol breakdown
    symbol_stats = _compute_symbol_stats(trades)
    symbol_rows  = _render_symbol_rows(symbol_stats)
    trade_rows   = _render_trade_rows(trades)
    go_nogo_items = "".join(
        f'<li style="color:{"#22c55e" if "✓" in r else "#ef4444"}">{r}</li>'
        for r in metrics.get("go_nogo_reasons", [])
    )

    exit_reasons = metrics.get("exit_reasons", {})
    exit_html = " | ".join(f"{k}: {v}" for k, v in exit_reasons.items()) or "—"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QuantSentinel Backtest Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }}
  h2 {{ font-size: 1.1rem; font-weight: 600; margin: 24px 0 12px; color: #94a3b8; }}
  .subtitle {{ color: #64748b; font-size: 0.875rem; margin-bottom: 24px; }}
  .verdict {{ display: inline-block; padding: 4px 14px; border-radius: 999px;
              font-weight: 700; font-size: 0.875rem;
              background: {verdict_color}22; color: {verdict_color};
              border: 1px solid {verdict_color}44; margin-bottom: 20px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 12px; margin-bottom: 24px; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px;
           padding: 16px; }}
  .card .label {{ font-size: 0.75rem; color: #64748b; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.4rem; font-weight: 700; }}
  .card .value.pos {{ color: #22c55e; }}
  .card .value.neg {{ color: #ef4444; }}
  .card .value.neu {{ color: #e2e8f0; }}
  .chart-wrap {{ background: #1e293b; border: 1px solid #334155;
                border-radius: 10px; padding: 20px; margin-bottom: 24px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
  th {{ background: #1e293b; color: #64748b; font-weight: 600; padding: 10px 12px;
        text-align: left; border-bottom: 1px solid #334155; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #1e293b; }}
  tr:hover td {{ background: #1e293b55; }}
  .win {{ color: #22c55e; }} .loss {{ color: #ef4444; }}
  .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
          font-size: 0.7rem; font-weight: 600; }}
  .tag-green  {{ background: #22c55e22; color: #22c55e; }}
  .tag-yellow {{ background: #f59e0b22; color: #f59e0b; }}
  .tag-red    {{ background: #ef444422; color: #ef4444; }}
  ul {{ list-style: none; padding: 0; }}
  ul li {{ padding: 4px 0; font-size: 0.9rem; }}
  .reasons {{ background: #1e293b; border: 1px solid #334155;
              border-radius: 10px; padding: 16px; margin-bottom: 24px; }}
</style>
</head>
<body>

<h1>QuantSentinel — Backtest Report</h1>
<p class="subtitle">
  Period: {cfg.get('start_date', '?')} → {cfg.get('end_date', '?')} &nbsp;|&nbsp;
  Capital: ₹{cfg.get('initial_capital', 0):,.0f} &nbsp;|&nbsp;
  Symbols: {len(cfg.get('symbols', []))} stocks &nbsp;|&nbsp;
  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p>

<div class="verdict">Verdict: {verdict}</div>

<!-- ── KEY METRICS ── -->
<div class="grid">
  <div class="card">
    <div class="label">Total Return</div>
    <div class="value {'pos' if metrics.get('total_return_pct', 0) >= 0 else 'neg'}">
      {metrics.get('total_return_pct', 0):+.1f}%
    </div>
  </div>
  <div class="card">
    <div class="label">CAGR</div>
    <div class="value {'pos' if metrics.get('cagr_pct', 0) >= 0 else 'neg'}">
      {metrics.get('cagr_pct', 0):+.1f}%
    </div>
  </div>
  <div class="card">
    <div class="label">Sharpe Ratio</div>
    <div class="value {'pos' if (metrics.get('sharpe_ratio') or 0) >= 0.8 else 'neg'}">
      {metrics.get('sharpe_ratio', float('nan')):.2f}
    </div>
  </div>
  <div class="card">
    <div class="label">Max Drawdown</div>
    <div class="value {'pos' if metrics.get('max_drawdown_pct', 100) < 15 else 'neg'}">
      -{metrics.get('max_drawdown_pct', 0):.1f}%
    </div>
  </div>
  <div class="card">
    <div class="label">Calmar Ratio</div>
    <div class="value neu">{metrics.get('calmar_ratio', 0):.2f}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value {'pos' if (metrics.get('win_rate') or 0) >= 0.5 else 'neg'}">
      {(metrics.get('win_rate') or 0)*100:.1f}%
    </div>
  </div>
  <div class="card">
    <div class="label">Total Trades</div>
    <div class="value neu">{metrics.get('total_trades', 0)}</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor</div>
    <div class="value {'pos' if (metrics.get('profit_factor') or 0) >= 1.5 else 'neg'}">
      {metrics.get('profit_factor', 0):.2f}
    </div>
  </div>
  <div class="card">
    <div class="label">Avg Hold (days)</div>
    <div class="value neu">{metrics.get('avg_holding_days', 0):.1f}</div>
  </div>
  <div class="card">
    <div class="label">Final Value</div>
    <div class="value neu">₹{metrics.get('final_value', 0):,.0f}</div>
  </div>
</div>

<!-- ── GO/NO-GO REASONS ── -->
<h2>Go / No-Go Assessment</h2>
<div class="reasons">
  <ul>{go_nogo_items}</ul>
  <p style="margin-top:10px; font-size:0.8rem; color:#64748b">
    Exit reasons: {exit_html}
  </p>
</div>

<!-- ── EQUITY CURVE ── -->
<h2>Equity Curve</h2>
<div class="chart-wrap">
  <canvas id="equityChart" height="80"></canvas>
</div>

<!-- ── PER-SYMBOL BREAKDOWN ── -->
<h2>Per-Symbol Performance</h2>
<table>
  <thead>
    <tr>
      <th>Symbol</th><th>Trades</th><th>Wins</th>
      <th>Win Rate</th><th>Total PnL (₹)</th><th>Avg PnL/Trade (₹)</th>
    </tr>
  </thead>
  <tbody>{symbol_rows}</tbody>
</table>

<!-- ── TRADE LOG ── -->
<h2>Trade Log ({len(trades)} trades)</h2>
<table>
  <thead>
    <tr>
      <th>Symbol</th><th>Entry Date</th><th>Exit Date</th>
      <th>Hold (d)</th><th>Entry ₹</th><th>Exit ₹</th>
      <th>PnL ₹</th><th>PnL %</th><th>Risk State</th><th>Exit Reason</th>
    </tr>
  </thead>
  <tbody>{trade_rows}</tbody>
</table>

<script>
const equity = {equity_json};
const labels = {dates_json};
new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{
    labels: labels,
    datasets: [{{
      label: 'Portfolio Value (₹)',
      data: equity,
      borderColor: '#6366f1',
      backgroundColor: 'rgba(99,102,241,0.08)',
      borderWidth: 2,
      pointRadius: 0,
      fill: true,
      tension: 0.3,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{
        ticks: {{ color: '#64748b', maxTicksLimit: 10 }},
        grid: {{ color: '#1e293b' }}
      }},
      y: {{
        ticks: {{ color: '#64748b',
                  callback: v => '₹' + v.toLocaleString('en-IN') }},
        grid: {{ color: '#334155' }}
      }}
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"HTML report written: {output_path}")
    return output_path


def generate_csv_report(results: dict, output_path: str = None) -> str:
    """Write the trade log as a CSV file for further analysis in Excel/pandas."""
    trades = results.get("trades", [])

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("reports")
        out_dir.mkdir(exist_ok=True)
        output_path = str(out_dir / f"trades_{ts}.csv")

    if not trades:
        log.warning("No trades to write to CSV")
        return output_path

    fieldnames = [
        "symbol", "entry_date", "exit_date", "holding_days",
        "qty", "entry_price", "exit_price", "pnl", "pnl_pct",
        "exit_reason", "risk_state",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trades)

    log.info(f"CSV report written: {output_path} ({len(trades)} trades)")
    return output_path


# =============================================================================
# HELPERS
# =============================================================================

def _compute_symbol_stats(trades: list[dict]) -> dict:
    """Aggregate trade stats per symbol."""
    stats = {}
    for t in trades:
        sym = t["symbol"]
        if sym not in stats:
            stats[sym] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        stats[sym]["trades"]    += 1
        stats[sym]["total_pnl"] += t.get("pnl", 0)
        if t.get("pnl", 0) > 0:
            stats[sym]["wins"] += 1
    return stats


def _render_symbol_rows(stats: dict) -> str:
    rows = []
    for sym, s in sorted(stats.items(), key=lambda x: -x[1]["total_pnl"]):
        wr  = s["wins"] / s["trades"] if s["trades"] > 0 else 0
        avg = s["total_pnl"] / s["trades"] if s["trades"] > 0 else 0
        cls = "win" if s["total_pnl"] > 0 else "loss"
        rows.append(
            f"<tr><td>{sym}</td><td>{s['trades']}</td><td>{s['wins']}</td>"
            f"<td>{wr*100:.0f}%</td>"
            f"<td class='{cls}'>₹{s['total_pnl']:+,.0f}</td>"
            f"<td class='{cls}'>₹{avg:+,.0f}</td></tr>"
        )
    return "".join(rows)


def _render_trade_rows(trades: list[dict]) -> str:
    rows = []
    for t in trades:
        cls = "win" if t.get("pnl", 0) > 0 else "loss"
        rs  = t.get("risk_state", "GREEN")
        rs_cls = {"GREEN": "tag-green", "YELLOW": "tag-yellow", "RED": "tag-red"}.get(rs, "tag-green")
        reason = (t.get("exit_reason", "") or "")[:35]
        rows.append(
            f"<tr>"
            f"<td><b>{t['symbol']}</b></td>"
            f"<td>{t.get('entry_date','')}</td>"
            f"<td>{t.get('exit_date','')}</td>"
            f"<td>{t.get('holding_days',0)}</td>"
            f"<td>₹{t.get('entry_price',0):.2f}</td>"
            f"<td>₹{t.get('exit_price',0):.2f}</td>"
            f"<td class='{cls}'>₹{t.get('pnl',0):+,.0f}</td>"
            f"<td class='{cls}'>{t.get('pnl_pct',0):+.1f}%</td>"
            f"<td><span class='tag {rs_cls}'>{rs}</span></td>"
            f"<td style='color:#94a3b8;font-size:0.75rem'>{reason}</td>"
            f"</tr>"
        )
    return "".join(rows)