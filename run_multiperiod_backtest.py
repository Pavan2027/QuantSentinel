# save as: run_multiperiod_backtest.py

from backtest.backtester import BacktestEngine, BacktestConfig
from backtest.report_generator import generate_html_report, generate_csv_report
from backtest.metrics import compute_all_metrics

# Use 20 NIFTY 50 stocks for a realistic test
SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "AXISBANK", "KOTAKBANK", "BHARTIARTL", "ITC",
    "HINDUNILVR", "TITAN", "WIPRO", "HCLTECH", "SUNPHARMA",
    "MARUTI", "BAJFINANCE", "LT", "NTPC", "POWERGRID",
]

PERIODS = [
    ("Bull Run",     "2023-07-01", "2023-12-31"),
    ("Mixed Market", "2024-01-01", "2024-06-30"),
    ("Bear Period",  "2024-07-01", "2024-12-31"),
]

print(f"\n{'='*70}")
print(f"{'MULTI-PERIOD BACKTEST':^70}")
print(f"{'='*70}")
print(f"{'Period':<20} {'Return':>8} {'Sharpe':>8} {'MaxDD':>8} "
      f"{'WinRate':>8} {'Trades':>8} {'Verdict':>12}")
print(f"{'-'*70}")

all_results = {}
for label, start, end in PERIODS:
    cfg = BacktestConfig(
        symbols=SYMBOLS,
        start_date=start,
        end_date=end,
        initial_capital=100_000.0,
        max_positions=5,
    )
    engine  = BacktestEngine(cfg)
    results = engine.run()
    m       = results["metrics"]
    all_results[label] = results

    verdict_icon = {"GO": "✅", "BORDERLINE": "🟡", "NO-GO": "❌"}.get(
        m["go_nogo"], "❓"
    )
    print(
        f"{label:<20} "
        f"{m['total_return_pct']:>+7.1f}% "
        f"{m['sharpe_ratio']:>8.2f} "
        f"{m['max_drawdown_pct']:>7.1f}% "
        f"{m.get('win_rate', 0)*100:>7.1f}% "
        f"{m['total_trades']:>8} "
        f"  {verdict_icon} {m['go_nogo']:>8}"
    )

print(f"{'='*70}\n")

# Generate HTML report for each period
for label, results in all_results.items():
    safe_label = label.lower().replace(" ", "_")
    path = generate_html_report(
        results,
        output_path=f"reports/backtest_{safe_label}.html"
    )
    print(f"Report: {path}")