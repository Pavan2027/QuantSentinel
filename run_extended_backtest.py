"""
run_extended_backtest.py
--------------------------
5-Year extended backtest across 250 stocks (NIFTY 100 + Midcap 100 + Smallcap 50).

Covers five annual market periods:
  1. 2021 H1: COVID recovery + bull run
  2. 2021 H2 – 2022 H1: Peak + correction
  3. 2022 H2 – 2023 H1: Bear market + bottoming
  4. 2023 H2 – 2024 H1: Recovery rally
  5. 2024 H2 – 2025 H1: Recent market

Uses dynamic sentiment proxy (price-based) for each period.

Usage:
    python run_extended_backtest.py              # Full 250-stock run
    python run_extended_backtest.py --quick      # Quick 50-stock NIFTY-only run
    python run_extended_backtest.py --tier large # Large-cap only (100 stocks)
"""

import sys
import time
from datetime import datetime

from backtest.backtester import BacktestEngine, BacktestConfig
from backtest.report_generator import generate_html_report
from strategy.universe import NIFTY_50, NIFTY_100, MIDCAP_100, SMALLCAP_50, ALL_STOCKS

# =============================================================================
# CONFIGURATION
# =============================================================================

# 5-year backtest periods
PERIODS = [
    ("2021-H1 Recovery",    "2021-01-01", "2021-06-30"),
    ("2021-H2 Peak",        "2021-07-01", "2021-12-31"),
    ("2022-H1 Correction",  "2022-01-01", "2022-06-30"),
    ("2022-H2 Bear",        "2022-07-01", "2022-12-31"),
    ("2023-H1 Bottoming",   "2023-01-01", "2023-06-30"),
    ("2023-H2 Bull Run",    "2023-07-01", "2023-12-31"),
    ("2024-H1 Mixed",       "2024-01-01", "2024-06-30"),
    ("2024-H2 Volatility",  "2024-07-01", "2024-12-31"),
    ("2025-H1 Recent",      "2025-01-01", "2025-04-18"),
]

# Capital allocation per tier
CAPITAL_CONFIG = {
    "full":  {"capital": 500_000, "max_pos": 10},
    "large": {"capital": 300_000, "max_pos": 8},
    "mid":   {"capital": 150_000, "max_pos": 6},
    "small": {"capital": 100_000, "max_pos": 5},
    "quick": {"capital": 100_000, "max_pos": 5},
}


def get_symbols(tier: str) -> list[str]:
    """Get stock list based on tier selection."""
    if tier == "full":
        return list(ALL_STOCKS)
    elif tier == "large":
        return list(NIFTY_100)
    elif tier == "mid":
        return list(MIDCAP_100)
    elif tier == "small":
        return list(SMALLCAP_50)
    elif tier == "quick":
        return list(NIFTY_50)[:20]  # Quick test with 20 stocks
    else:
        return list(ALL_STOCKS)


def run_backtest(symbols, periods, capital, max_pos, tier_name):
    """Run backtest across all periods and return results."""
    print(f"\n{'='*80}")
    print(f"{'EXTENDED 5-YEAR BACKTEST':^80}")
    print(f"{'='*80}")
    print(f"  Universe:  {len(symbols)} stocks ({tier_name})")
    print(f"  Capital:   INR{capital:,.0f}")
    print(f"  Positions: {max_pos} max concurrent")
    print(f"  Sentiment: Dynamic (price-based proxy)")
    print(f"  Periods:   {len(periods)}")
    print(f"{'='*80}\n")

    # Header
    print(f"{'Period':<25} {'Return':>8} {'Sharpe':>8} {'MaxDD':>8} "
          f"{'WinRate':>8} {'Trades':>8} {'Verdict':>12}")
    print(f"{'-'*80}")

    all_results = {}
    total_start = time.time()

    for label, start, end in periods:
        period_start = time.time()

        cfg = BacktestConfig(
            symbols=symbols,
            start_date=start,
            end_date=end,
            initial_capital=capital,
            max_positions=max_pos,
            use_sentiment=True,
        )
        engine = BacktestEngine(cfg)
        results = engine.run()

        if "error" in results:
            print(f"{label:<25}  [!]  {results['error']}")
            continue

        m = results["metrics"]
        all_results[label] = results

        verdict_icon = {"GO": "[OK]", "BORDERLINE": "[~~]", "NO-GO": "[XX]"}.get(
            m["go_nogo"], "[??]"
        )

        elapsed = time.time() - period_start
        print(
            f"{label:<25} "
            f"{m['total_return_pct']:>+7.1f}% "
            f"{m['sharpe_ratio']:>8.2f} "
            f"{m['max_drawdown_pct']:>7.1f}% "
            f"{m.get('win_rate', 0)*100:>7.1f}% "
            f"{m['total_trades']:>8} "
            f"  {verdict_icon} {m['go_nogo']:>8}"
            f"  ({elapsed:.0f}s)"
        )

    total_elapsed = time.time() - total_start
    print(f"{'='*80}")
    print(f"  Total time: {total_elapsed:.0f}s")

    # Summary statistics across all periods
    if all_results:
        returns = [r["metrics"]["total_return_pct"] for r in all_results.values()]
        sharpes = [r["metrics"]["sharpe_ratio"] for r in all_results.values()]
        win_rates = [r["metrics"].get("win_rate", 0) * 100 for r in all_results.values()]
        verdicts = [r["metrics"]["go_nogo"] for r in all_results.values()]

        print(f"\n{'AGGREGATE SUMMARY':^80}")
        print(f"{'-'*80}")
        print(f"  Avg Return:    {sum(returns)/len(returns):>+.1f}%")
        print(f"  Avg Sharpe:    {sum(sharpes)/len(sharpes):>.2f}")
        print(f"  Avg Win Rate:  {sum(win_rates)/len(win_rates):>.1f}%")
        print(f"  GO:            {verdicts.count('GO')}/{len(verdicts)}")
        print(f"  BORDERLINE:    {verdicts.count('BORDERLINE')}/{len(verdicts)}")
        print(f"  NO-GO:         {verdicts.count('NO-GO')}/{len(verdicts)}")
        print(f"{'='*80}\n")

    return all_results


def generate_reports(all_results, tier_name):
    """Generate HTML reports for each period."""
    print("Generating reports...")
    for label, results in all_results.items():
        safe_label = label.lower().replace(" ", "_").replace("-", "_")
        path = generate_html_report(
            results,
            output_path=f"reports/extended_{tier_name}_{safe_label}.html"
        )
        print(f"  [R] {path}")
    print()


def main():
    # Parse args
    tier = "quick"  # default to quick for safety
    if "--quick" in sys.argv:
        tier = "quick"
    elif "--tier" in sys.argv:
        idx = sys.argv.index("--tier")
        if idx + 1 < len(sys.argv):
            tier = sys.argv[idx + 1]
    elif "--full" in sys.argv:
        tier = "full"

    symbols = get_symbols(tier)
    config = CAPITAL_CONFIG.get(tier, CAPITAL_CONFIG["quick"])

    results = run_backtest(
        symbols=symbols,
        periods=PERIODS,
        capital=config["capital"],
        max_pos=config["max_pos"],
        tier_name=tier,
    )

    if results:
        generate_reports(results, tier)

    # Save summary to text file
    with open("backtest_extended_results.txt", "w", encoding="utf-8") as f:
        f.write(f"Extended Backtest Results - {tier}\n")
        f.write(f"Run at: {datetime.now().isoformat()}\n")
        f.write(f"Universe: {len(symbols)} stocks\n\n")
        for label, r in results.items():
            m = r["metrics"]
            f.write(
                f"{label:<25} "
                f"Return={m['total_return_pct']:>+.1f}% "
                f"Sharpe={m['sharpe_ratio']:.2f} "
                f"MaxDD={m['max_drawdown_pct']:.1f}% "
                f"WinRate={m.get('win_rate', 0)*100:.1f}% "
                f"Trades={m['total_trades']} "
                f"Verdict={m['go_nogo']}\n"
            )


if __name__ == "__main__":
    main()
