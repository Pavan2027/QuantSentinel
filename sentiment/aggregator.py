"""
sentiment/aggregator.py
------------------------
Aggregates FinBERT-scored headlines into a single per-stock sentiment score.

Pipeline:
  1. News records come in from news_provider (with staleness_hrs, decay_weight)
  2. FinBERT scores each headline (confidence-gated)
  3. Aggregator combines scores weighted by:
        - staleness decay weight (fresh news counts more)
        - sentiment confidence (high-confidence predictions count more)
  4. Optional: social sentiment from social_provider gets blended at low weight
  5. Output: single float in [0, 1] per stock, ready for scoring.py

Score interpretation:
  > 0.65  → bullish sentiment
  0.4–0.65 → neutral
  < 0.40  → bearish sentiment

If no reliable news exists, returns 0.5 (neutral default).
"""

from utils.logger import get_logger
from features.preprocessing import aggregate_sentiment_scores

log = get_logger("aggregator")

# Weight for social sentiment vs news sentiment in final blend
SOCIAL_WEIGHT = 0.10   # 10% — social is a weak signal
NEWS_WEIGHT   = 0.90   # 90% — news is the primary signal

# Minimum number of trusted headlines to produce a reliable score
MIN_TRUSTED_HEADLINES = 1

# If all headlines are low-confidence, return this neutral default
NEUTRAL_DEFAULT = 0.5


def aggregate_stock_sentiment(scored_news: list[dict],
                               social_score: float = None) -> dict:
    """
    Combine scored headlines into a single stock sentiment result.

    Args:
        scored_news:   Output of FinBERTModel.score_stock_news()
                       Each item has: sentiment_score, decay_weight,
                       sentiment_trusted, sentiment_confidence
        social_score:  Optional pre-computed social sentiment (0–1)

    Returns:
        {
          "sentiment_score":    float (0–1),
          "sentiment_label":    "bullish" | "neutral" | "bearish",
          "headline_count":     int,
          "trusted_count":      int,
          "avg_confidence":     float,
          "news_score":         float,
          "social_score":       float | None,
          "data_quality":       "good" | "limited" | "insufficient",
        }
    """
    if not scored_news:
        log.debug("No scored news — returning neutral")
        return _neutral_result(headline_count=0)

    trusted = [
        item for item in scored_news
        if item.get("sentiment_trusted", False)
        and item.get("decay_weight", 0) > 0.1
    ]

    headline_count = len(scored_news)
    trusted_count  = len(trusted)

    if trusted_count < MIN_TRUSTED_HEADLINES:
        log.debug(
            f"Only {trusted_count} trusted headlines "
            f"(of {headline_count}) — returning neutral"
        )
        return _neutral_result(headline_count=headline_count,
                               trusted_count=trusted_count,
                               data_quality="insufficient")

    # Weighted average:  weight = decay_weight × confidence
    # This gives most influence to fresh, high-confidence predictions
    weighted_sum  = 0.0
    total_weight  = 0.0
    confidences   = []

    for item in trusted:
        score      = item.get("sentiment_score", 0.5)
        decay_w    = item.get("decay_weight", 1.0)
        confidence = item.get("sentiment_confidence", 0.5)
        combined_w = decay_w * confidence

        weighted_sum += score * combined_w
        total_weight += combined_w
        confidences.append(confidence)

    news_score    = round(weighted_sum / total_weight, 4) if total_weight > 0 else NEUTRAL_DEFAULT
    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    # Blend with social if available
    if social_score is not None and 0.0 <= social_score <= 1.0:
        final_score = round(
            NEWS_WEIGHT * news_score + SOCIAL_WEIGHT * social_score, 4
        )
    else:
        final_score = news_score

    # Clamp to [0, 1]
    final_score = min(1.0, max(0.0, final_score))

    # Label
    if final_score >= 0.65:
        label = "bullish"
    elif final_score <= 0.40:
        label = "bearish"
    else:
        label = "neutral"

    # Data quality assessment
    if trusted_count >= 5:
        quality = "good"
    elif trusted_count >= 2:
        quality = "limited"
    else:
        quality = "insufficient"

    result = {
        "sentiment_score":  final_score,
        "sentiment_label":  label,
        "headline_count":   headline_count,
        "trusted_count":    trusted_count,
        "avg_confidence":   avg_confidence,
        "news_score":       news_score,
        "social_score":     social_score,
        "data_quality":     quality,
    }

    log.debug(
        f"Sentiment: score={final_score:.3f} ({label}), "
        f"trusted={trusted_count}/{headline_count}, "
        f"avg_conf={avg_confidence:.2f}, quality={quality}"
    )

    return result


def aggregate_universe_sentiment(finbert_model,
                                  stock_news_map: dict[str, list[dict]],
                                  social_map: dict[str, float] = None) -> dict[str, dict]:
    """
    Aggregate sentiment for an entire universe of stocks.

    Args:
        finbert_model:  FinBERTModel instance
        stock_news_map: {symbol: [news_items]} from news_provider
        social_map:     {symbol: score} optional social scores

    Returns:
        {symbol: sentiment_result_dict}
    """
    if social_map is None:
        social_map = {}

    results = {}
    total   = len(stock_news_map)

    for i, (symbol, news_items) in enumerate(stock_news_map.items(), 1):
        log.info(f"Scoring sentiment [{i}/{total}]: {symbol} "
                 f"({len(news_items)} headlines)")

        if not news_items:
            results[symbol] = _neutral_result(headline_count=0)
            continue

        # Score headlines with FinBERT
        scored = finbert_model.score_stock_news(news_items)

        # Aggregate
        results[symbol] = aggregate_stock_sentiment(
            scored,
            social_score=social_map.get(symbol),
        )

    log.info(f"Sentiment aggregation complete: {total} stocks scored")
    return results


def get_sentiment_scores_only(sentiment_results: dict[str, dict]) -> dict[str, float]:
    """
    Extract just the float score from a universe sentiment result dict.
    Convenience function for passing to scoring.score_all_stocks().

    Returns:
        {symbol: score}  where score is in [0, 1]
    """
    return {
        sym: result.get("sentiment_score", NEUTRAL_DEFAULT)
        for sym, result in sentiment_results.items()
    }


def describe_sentiment(score: float) -> str:
    """Human-readable label for a sentiment score. Used in UI and logs."""
    if score >= 0.80:
        return "Strongly Bullish"
    if score >= 0.65:
        return "Bullish"
    if score >= 0.55:
        return "Slightly Bullish"
    if score >= 0.45:
        return "Neutral"
    if score >= 0.30:
        return "Slightly Bearish"
    if score >= 0.20:
        return "Bearish"
    return "Strongly Bearish"


def _neutral_result(headline_count: int = 0,
                     trusted_count: int = 0,
                     data_quality: str = "insufficient") -> dict:
    return {
        "sentiment_score":  NEUTRAL_DEFAULT,
        "sentiment_label":  "neutral",
        "headline_count":   headline_count,
        "trusted_count":    trusted_count,
        "avg_confidence":   0.0,
        "news_score":       NEUTRAL_DEFAULT,
        "social_score":     None,
        "data_quality":     data_quality,
    }