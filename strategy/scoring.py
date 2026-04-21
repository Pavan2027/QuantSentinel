"""
strategy/scoring.py
--------------------
Computes the final composite score for each stock.

Scoring is risk-state-aware:
  - GREEN:  sentiment-heavy, momentum-driven
  - YELLOW: balanced, start penalizing volatility
  - RED:    strongly prefers calm, liquid, large-cap stocks

All input signals are in [0, 1]. Output score is in [0, 1].
Sentiment defaults to 0.5 (neutral) until Phase 3 adds FinBERT.
"""

from utils.logger import get_logger

log = get_logger("scoring")

# =============================================================================
# WEIGHT PROFILES PER RISK STATE
# =============================================================================
# Each profile sums to 1.0. ATR score = inverse volatility (higher = calmer).
# In RED state, we strongly weight ATR to filter out volatile stocks.

WEIGHT_PROFILES = {
    "GREEN": {
        "sentiment":  0.10,
        "momentum":   0.30,
        "rsi":        0.20,
        "volume":     0.15,
        "atr":        0.10,
        "macd":       0.15,
    },
    "YELLOW": {
        "sentiment":  0.10,
        "momentum":   0.20,
        "rsi":        0.20,
        "volume":     0.12,
        "atr":        0.25,
        "macd":       0.13,
    },
    "RED": {
        "sentiment":  0.05,
        "momentum":   0.10,
        "rsi":        0.20,
        "volume":     0.12,
        "atr":        0.40,   # strongly prefer calm stocks in RED state
        "macd":       0.13,
    },
}

# Buy threshold per risk state — harder to trigger a BUY in stressed states
BUY_THRESHOLD = {
    "GREEN":  0.52,
    "YELLOW": 0.58,
    "RED":    0.65,
}

# Maximum days to hold a position per risk state
MAX_HOLD_DAYS = {
    "GREEN":  25,
    "YELLOW": 15,
    "RED":    8,
}

# Stop loss percentage per risk state (tighter in stressed states)
STOP_LOSS_PCT = {
    "GREEN":  0.08,   # 8% stop loss
    "YELLOW": 0.06,   # 6%
    "RED":    0.04,   # 4%
}

# Take profit target per risk state
TAKE_PROFIT_PCT = {
    "GREEN":  0.18,   # 18% target
    "YELLOW": 0.12,   # 12%
    "RED":    0.07,   # 7%
}

# Trailing stop: activated when price is this far above entry
TRAILING_STOP_ACTIVATION_PCT = {
    "GREEN":  0.06,
    "YELLOW": 0.04,
    "RED":    0.03,
}

TRAILING_STOP_DISTANCE_PCT = {
    "GREEN":  0.04,
    "YELLOW": 0.03,
    "RED":    0.02,
}


# =============================================================================
# CORE SCORING FUNCTION
# =============================================================================

def compute_score(signals: dict,
                  risk_state: str = "GREEN",
                  sentiment_score: float = 0.5) -> float:
    """
    Compute the composite score for a stock.

    Args:
        signals:         Output dict from features.technicals.compute_all_signals()
        risk_state:      "GREEN", "YELLOW", or "RED"
        sentiment_score: Float in [0, 1] from FinBERT (defaults to 0.5 = neutral)

    Returns:
        Composite score in [0, 1]
    """
    if risk_state not in WEIGHT_PROFILES:
        log.warning(f"Unknown risk state '{risk_state}', defaulting to GREEN")
        risk_state = "GREEN"

    weights = WEIGHT_PROFILES[risk_state]

    score = (
        weights["sentiment"] * sentiment_score
        + weights["momentum"] * signals.get("momentum_score", 0.5)
        + weights["rsi"]      * signals.get("rsi_score", 0.5)
        + weights["volume"]   * signals.get("volume_score", 0.5)
        + weights["atr"]      * signals.get("atr_score", 0.5)
        + weights["macd"]     * signals.get("macd_score", 0.5)
    )

    return round(min(max(score, 0.0), 1.0), 4)


def score_all_stocks(stock_signals: dict[str, dict],
                     risk_state: str = "GREEN",
                     sentiment_scores: dict[str, float] = None) -> list[dict]:
    """
    Score and rank a universe of stocks.

    Args:
        stock_signals:    {symbol: signals_dict} from compute_all_signals()
        risk_state:       Current risk state
        sentiment_scores: {symbol: float} optional FinBERT scores

    Returns:
        List of dicts sorted by score descending:
        [{"symbol": str, "score": float, "signals": dict}, ...]
    """
    if sentiment_scores is None:
        sentiment_scores = {}

    results = []
    for symbol, signals in stock_signals.items():
        if signals is None:
            continue
        sentiment = sentiment_scores.get(symbol, 0.5)
        score = compute_score(signals, risk_state, sentiment)
        results.append({
            "symbol":    symbol,
            "score":     score,
            "signals":   signals,
            "sentiment": sentiment,
        })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    log.info(
        f"Scored {len(results)} stocks in {risk_state} state. "
        f"Top: {results[0]['symbol']}={results[0]['score']:.3f}"
        if results else f"No stocks scored"
    )

    return results


def get_top_picks(ranked_stocks: list[dict],
                  risk_state: str = "GREEN",
                  n: int = None) -> list[dict]:
    """
    Return the top N stocks that exceed the buy threshold for the risk state.

    Args:
        ranked_stocks: Output of score_all_stocks()
        risk_state:    Current risk state
        n:             Max picks (defaults: GREEN=5, YELLOW=4, RED=3)

    Returns:
        Filtered and capped list of top picks
    """
    if n is None:
        n = {"GREEN": 5, "YELLOW": 4, "RED": 3}.get(risk_state, 5)

    threshold = BUY_THRESHOLD[risk_state]
    eligible = [s for s in ranked_stocks if s["score"] >= threshold]
    picks = eligible[:n]

    log.info(
        f"Top picks ({risk_state}): {len(picks)}/{len(ranked_stocks)} eligible "
        f"above threshold {threshold}. "
        + (f"Best: {picks[0]['symbol']}={picks[0]['score']:.3f}" if picks else "None")
    )

    return picks


def compute_exit_levels(entry_price: float,
                         atr: float,
                         risk_state: str = "GREEN") -> dict:
    """
    Compute stop loss, take profit, and trailing stop levels at entry.

    Uses ATR-based stop: stop = entry - 2*ATR (but capped by percentage floor).
    Take profit is percentage-based.

    Returns:
        {stop_loss, take_profit, trailing_stop_activation, trailing_stop_distance}
    """
    pct_stop    = STOP_LOSS_PCT[risk_state]
    pct_target  = TAKE_PROFIT_PCT[risk_state]
    trail_act   = TRAILING_STOP_ACTIVATION_PCT[risk_state]
    trail_dist  = TRAILING_STOP_DISTANCE_PCT[risk_state]

    # ATR-based stop (2x ATR), but at least the percentage floor
    atr_stop = entry_price - (2 * atr)
    pct_stop_price = entry_price * (1 - pct_stop)
    stop_loss = max(atr_stop, pct_stop_price)   # tighter of the two

    take_profit = entry_price * (1 + pct_target)

    return {
        "stop_loss":                  round(stop_loss, 2),
        "take_profit":                round(take_profit, 2),
        "trailing_stop_activation":   round(entry_price * (1 + trail_act), 2),
        "trailing_stop_distance_pct": trail_dist,
    }