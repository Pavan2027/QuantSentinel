"""
backtest/metrics.py
--------------------
Performance metric calculations for backtest results.

Outputs:
  - Sharpe ratio     (annualized, risk-free = 6.5% for India)
  - Maximum drawdown (% from peak to trough)
  - CAGR             (compound annual growth rate)
  - Calmar ratio     (CAGR / max drawdown)
  - Win rate, avg win %, avg loss %
  - Profit factor    (gross profit / gross loss)
  - Average holding period
  - Go/No-Go verdict for strategy readiness
"""

import numpy as np

RISK_FREE_RATE_ANNUAL = 0.065   # India 10-year G-sec approx
TRADING_DAYS_PER_YEAR = 252


def compute_sharpe(equity_curve: list[float],
                   risk_free_annual: float = RISK_FREE_RATE_ANNUAL) -> float:
    """Annualized Sharpe ratio from a daily equity curve."""
    if len(equity_curve) < 2:
        return float("nan")
    returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
    if np.std(returns) == 0:
        return float("nan")
    daily_rf = risk_free_annual / TRADING_DAYS_PER_YEAR
    sharpe = (np.mean(returns - daily_rf) / np.std(returns)) * np.sqrt(TRADING_DAYS_PER_YEAR)
    return round(float(sharpe), 4)


def compute_max_drawdown(equity_curve: list[float]) -> dict:
    """
    Maximum peak-to-trough drawdown.

    Returns:
        max_drawdown_pct: negative float, e.g. -0.15 = 15% drawdown
        peak_idx, trough_idx, recovery_idx
    """
    if len(equity_curve) < 2:
        return {"max_drawdown_pct": 0.0, "peak_idx": 0,
                "trough_idx": 0, "recovery_idx": None}

    arr = np.array(equity_curve, dtype=float)
    peak, peak_idx = arr[0], 0
    max_dd, max_peak_idx, max_trough_idx = 0.0, 0, 0

    for i in range(1, len(arr)):
        if arr[i] > peak:
            peak, peak_idx = arr[i], i
        dd = (arr[i] - peak) / peak
        if dd < max_dd:
            max_dd, max_peak_idx, max_trough_idx = dd, peak_idx, i

    recovery_idx = None
    if max_trough_idx < len(arr) - 1:
        peak_val = arr[max_peak_idx]
        for i in range(max_trough_idx + 1, len(arr)):
            if arr[i] >= peak_val:
                recovery_idx = i
                break

    return {
        "max_drawdown_pct": round(float(max_dd), 4),
        "peak_idx":         max_peak_idx,
        "trough_idx":       max_trough_idx,
        "recovery_idx":     recovery_idx,
    }


def compute_cagr(initial: float, final: float, num_trading_days: int) -> float:
    """Compound Annual Growth Rate over a number of trading days."""
    if initial <= 0 or num_trading_days <= 0:
        return float("nan")
    years = num_trading_days / TRADING_DAYS_PER_YEAR
    return round(float((final / initial) ** (1 / years) - 1), 4)


def compute_trade_metrics(trades: list[dict]) -> dict:
    """
    Trade-level statistics from a list of completed trade dicts.

    Each trade expects keys: pnl, entry_price, qty, holding_days, exit_reason
    """
    completed = [t for t in trades if t.get("pnl") is not None]

    if not completed:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": float("nan"), "avg_win_pct": float("nan"),
            "avg_loss_pct": float("nan"), "profit_factor": float("nan"),
            "avg_holding_days": float("nan"), "exit_reasons": {},
        }

    winners = [t for t in completed if t["pnl"] > 0]
    losers  = [t for t in completed if t["pnl"] <= 0]

    def pct(t):
        denom = t.get("entry_price", 1) * t.get("qty", 1)
        return t["pnl"] / denom if denom != 0 else 0

    avg_win_pct  = round(float(np.mean([pct(t) for t in winners])), 4) if winners else float("nan")
    avg_loss_pct = round(float(np.mean([pct(t) for t in losers])), 4)  if losers  else float("nan")

    gross_profit = sum(t["pnl"] for t in winners)
    gross_loss   = abs(sum(t["pnl"] for t in losers))
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")

    avg_hold = round(float(np.mean([t.get("holding_days", 0) for t in completed])), 1)

    exit_reasons = {}
    for t in completed:
        key = t.get("exit_reason", "unknown").split(" ")[0]
        exit_reasons[key] = exit_reasons.get(key, 0) + 1

    return {
        "total_trades":     len(completed),
        "winning_trades":   len(winners),
        "losing_trades":    len(losers),
        "win_rate":         round(len(winners) / len(completed), 4),
        "avg_win_pct":      avg_win_pct,
        "avg_loss_pct":     avg_loss_pct,
        "profit_factor":    profit_factor,
        "avg_holding_days": avg_hold,
        "exit_reasons":     exit_reasons,
    }


def _assess_go_nogo(sharpe: float, max_dd_pct: float, win_rate: float) -> dict:
    """
    Evaluate whether the strategy passes go/no-go criteria.
    All three must pass for GO:
      - Sharpe > 0.8
      - Max drawdown < 20%
      - Win rate > 45%
    """
    reasons, failures = [], 0

    if np.isnan(sharpe) or sharpe < 0.8:
        reasons.append(f"Sharpe {sharpe:.2f} < 0.8 ✗"); failures += 1
    else:
        reasons.append(f"Sharpe {sharpe:.2f} ✓")

    if max_dd_pct > 20.0:
        reasons.append(f"Max drawdown {max_dd_pct:.1f}% > 20% ✗"); failures += 1
    else:
        reasons.append(f"Max drawdown {max_dd_pct:.1f}% ✓")

    if np.isnan(win_rate) or win_rate < 0.45:
        wr = win_rate * 100 if not np.isnan(win_rate) else float("nan")
        reasons.append(f"Win rate {wr:.1f}% < 45% ✗"); failures += 1
    else:
        reasons.append(f"Win rate {win_rate*100:.1f}% ✓")

    verdict = "GO" if failures == 0 else ("BORDERLINE" if failures == 1 else "NO-GO")
    return {"verdict": verdict, "reasons": reasons}


def compute_all_metrics(equity_curve: list[float],
                         trades: list[dict],
                         initial_capital: float) -> dict:
    """
    Compute all performance metrics and return a unified summary dict.
    This is the main entry point used by the report generator.
    """
    if not equity_curve:
        return {"error": "No equity curve data"}

    final_value  = equity_curve[-1]
    total_return = (final_value - initial_capital) / initial_capital
    sharpe       = compute_sharpe(equity_curve)
    drawdown     = compute_max_drawdown(equity_curve)
    cagr         = compute_cagr(initial_capital, final_value, len(equity_curve))
    trade_mets   = compute_trade_metrics(trades)

    max_dd_pct = abs(drawdown["max_drawdown_pct"]) * 100
    calmar = round(cagr / (max_dd_pct / 100), 4) if max_dd_pct > 0 else float("inf")

    go_nogo = _assess_go_nogo(sharpe, max_dd_pct,
                               trade_mets.get("win_rate", float("nan")))

    return {
        "initial_capital":    initial_capital,
        "final_value":        round(final_value, 2),
        "total_return_pct":   round(total_return * 100, 2),
        "cagr_pct":           round(cagr * 100, 2),
        "sharpe_ratio":       sharpe,
        "calmar_ratio":       calmar,
        "max_drawdown_pct":   round(max_dd_pct, 2),
        "drawdown_peak_idx":  drawdown["peak_idx"],
        "drawdown_trough_idx": drawdown["trough_idx"],
        **trade_mets,
        "go_nogo":            go_nogo["verdict"],
        "go_nogo_reasons":    go_nogo["reasons"],
    }