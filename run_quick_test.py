"""Quick test to capture current backtest results."""
import logging
logging.disable(logging.CRITICAL)

import json
from backtest.backtester import BacktestEngine, BacktestConfig

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

output_lines = []
for label, start, end in PERIODS:
    cfg = BacktestConfig(
        symbols=SYMBOLS,
        start_date=start,
        end_date=end,
        initial_capital=100_000.0,
        max_positions=5,
    )
    engine = BacktestEngine(cfg)
    results = engine.run()
    m = results["metrics"]
    output_lines.append(f"{label}:")
    output_lines.append(f"  Return={m['total_return_pct']:+.1f}%")
    output_lines.append(f"  Sharpe={m['sharpe_ratio']:.2f}")
    output_lines.append(f"  MaxDD={m['max_drawdown_pct']:.1f}%")
    output_lines.append(f"  WinRate={m.get('win_rate', 0)*100:.1f}%")
    output_lines.append(f"  Trades={m['total_trades']}")
    output_lines.append(f"  Verdict={m['go_nogo']}")
    output_lines.append(f"  Exit reasons: {m.get('exit_reasons', {})}")
    output_lines.append(f"  AvgHold={m.get('avg_holding_days','?')}d")
    output_lines.append(f"  AvgWin={m.get('avg_win_pct',0)*100:.1f}%")
    output_lines.append(f"  AvgLoss={m.get('avg_loss_pct',0)*100:.1f}%")
    output_lines.append(f"  PF={m.get('profit_factor','?')}")
    output_lines.append("")

with open("backtest_results.txt", "w") as f:
    f.write("\n".join(output_lines))
print("Results written to backtest_results.txt")
