"""
data/social_provider.py
------------------------
Twitter/X social sentiment provider.
Currently a stub — social data has low weight in our scoring model
and Twitter's API costs are high. Enable when you have a Bearer Token.

Design intent:
  - Used as a filter/signal boost, NOT as a primary decision driver
  - Weight in scoring: 0.05–0.10 max
  - Sentiment here should corroborate news sentiment, never override it
"""

import requests
from datetime import datetime, timedelta, timezone

from config.settings import TWITTER_BEARER_TOKEN
from utils.logger import get_logger

log = get_logger("social_provider")

TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

# NSE-focused accounts to track (high signal, low noise)
TRUSTED_FINANCIAL_ACCOUNTS = [
    "NSEIndia",
    "BSEIndia",
    "SEBI_India",
    "RBI",
    "MoneycontrolCom",
    "livemint",
    "economictimes",
]


def _build_query(symbol: str, company_name: str = None) -> str:
    """Build a Twitter search query focused on financial discourse."""
    name_part = f'"{company_name}"' if company_name else f"#{symbol}"
    account_filter = " OR ".join(f"from:{a}" for a in TRUSTED_FINANCIAL_ACCOUNTS)
    return f"({name_part} OR ${symbol}) ({account_filter}) -is:retweet lang:en"


def get_social_sentiment(symbol: str,
                          company_name: str = None,
                          lookback_hours: int = 6) -> dict:
    """
    Fetch recent tweets about a stock from trusted financial accounts.

    Returns:
        {
          "available":   bool,
          "tweet_count": int,
          "tweets":      list of {"text": str, "created_at": str},
          "raw_score":   float | None,  # filled by FinBERT in Phase 3
        }

    If TWITTER_BEARER_TOKEN is not set, returns a neutral stub.
    """
    if not TWITTER_BEARER_TOKEN:
        log.debug(f"Twitter token not set — returning stub for {symbol}")
        return {
            "available":   False,
            "tweet_count": 0,
            "tweets":      [],
            "raw_score":   None,
        }

    start_time = (
        datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    query = _build_query(symbol, company_name)
    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    params = {
        "query":      query,
        "max_results": 20,
        "start_time": start_time,
        "tweet.fields": "created_at,text",
    }

    try:
        resp = requests.get(
            TWITTER_SEARCH_URL, headers=headers, params=params, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.error(f"Twitter API error for {symbol}: {e}")
        return {"available": False, "tweet_count": 0, "tweets": [], "raw_score": None}

    tweets = data.get("data", [])
    log.info(f"Social: {len(tweets)} tweets fetched for {symbol}")

    return {
        "available":   True,
        "tweet_count": len(tweets),
        "tweets":      [{"text": t["text"], "created_at": t.get("created_at", "")}
                        for t in tweets],
        "raw_score":   None,   # FinBERT fills this in Phase 3
    }