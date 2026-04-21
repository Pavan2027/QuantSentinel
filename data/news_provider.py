"""
data/news_provider.py
----------------------
Multi-source financial news aggregator with dynamic aliasing.

Features:
- Dynamic alias generation (no hardcoding)
- NewsAPI + NewsData + Marketaux
- Multi-query expansion
- Cross-source deduplication
- Staleness decay weighting
- Fallback handling
- Caching (including empty results)
"""

import hashlib
import re
import requests
from datetime import datetime, timedelta, timezone
import feedparser

from config.settings import (
    NEWS_API_KEY,
    NEWSDATA_API_KEY,
    MARKETAUX_API_KEY,
    NEWS_LOOKBACK_HOURS,
    NEWS_STALENESS_CUTOFF_HOURS,
    MAX_HEADLINES_PER_STOCK,
    CACHE_TTL_NEWS_SECS,
)
from data.cache import Cache
from utils.logger import get_logger

log = get_logger("news_provider")
cache = Cache()

# APIs
NEWSAPI_BASE = "https://newsapi.org/v2/everything"
NEWSDATA_BASE = "https://newsdata.io/api/1/news"
MARKETAUX_BASE = "https://api.marketaux.com/v1/news/all"

FIN_TERMS = ["stock", "shares", "earnings", "results", "revenue", "profit", "India"]

RSS_FEEDS = {
    "economic_times": "https://economictimes.indiatimes.com/markets/rss.cms",
    "moneycontrol":   "https://www.moneycontrol.com/rss/marketsinformation.xml",
    "livemint":       "https://www.livemint.com/rss/markets",
}

# -------------------- TEXT HELPERS --------------------

def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()[:16]


# -------------------- TIME HELPERS --------------------

def _hours_ago(hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


from datetime import datetime, timezone

def _parse_date(raw: str):
    if not raw:
        return None

    try:
        # Original NewsAPI format
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt.replace(tzinfo=timezone.utc)

    except ValueError:
        try:
            # Fallback for other APIs
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))

            # 🔥 Fix: ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

        except Exception:
            return None

    return dt


def _staleness_hours(published_at):
    if not published_at:
        return float("inf")
    return (datetime.now(timezone.utc) - published_at).total_seconds() / 3600


def _decay(hours_old: float) -> float:
    if hours_old <= NEWS_STALENESS_CUTOFF_HOURS:
        return 1.0
    if hours_old >= NEWS_LOOKBACK_HOURS:
        return 0.0
    return 1 - (hours_old - NEWS_STALENESS_CUTOFF_HOURS) / (
        NEWS_LOOKBACK_HOURS - NEWS_STALENESS_CUTOFF_HOURS
    )


# -------------------- DYNAMIC ALIASING --------------------

def _generate_aliases(symbol: str, company_name: str | None):
    """
    Generate aliases dynamically from company name.
    Avoids hardcoding large alias dictionaries.
    """
    aliases = set()

    if company_name:
        clean_name = re.sub(r"\b(Limited|Ltd|Inc|Corp|Corporation)\b", "", company_name, flags=re.IGNORECASE).strip()
        words = clean_name.split()

        # Full and cleaned names
        aliases.add(company_name)
        aliases.add(clean_name)

        # First word (most important for Indian stocks)
        if words:
            aliases.add(words[0])

        # Acronym (e.g. Reliance Industries -> RI, sometimes useful)
        acronym = "".join(w[0] for w in words if w[0].isalpha())
        if len(acronym) >= 2:
            aliases.add(acronym)

    # Always include symbol
    aliases.add(symbol)

    return list(aliases)


def _build_queries(symbol: str, company_name: str | None):
    aliases = _generate_aliases(symbol, company_name)

    # Limit to 2 best queries to avoid API overload
    primary = aliases[0] if aliases else symbol
    queries = [
        f'"{primary}" AND ({" OR ".join(FIN_TERMS)})',
        f'{primary} stock India',
    ]

    return queries


# -------------------- API FETCHERS --------------------


def _fetch_rss(feed_url: str, symbol: str, company_name: str) -> list[dict]:
    """Fetch and filter RSS feed for a specific stock."""
    try:
        # Use requests with timeout first, then parse the content
        resp = requests.get(feed_url, timeout=5)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        results = []
        search_terms = [symbol.lower()]
        if company_name:
            search_terms.append(company_name.lower().split()[0])
        
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = (title + " " + summary).lower()
            
            # Only include if stock is mentioned
            if not any(term in text for term in search_terms):
                continue
                
            published = entry.get("published_parsed")
            if published:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(
                    __import__("time").mktime(published), tz=timezone.utc
                )
                staleness = _staleness_hours(dt)
                if staleness > NEWS_LOOKBACK_HOURS:
                    continue
            else:
                dt = None
                staleness = NEWS_STALENESS_CUTOFF_HOURS  # assume fresh if no date

            results.append({
                "headline":     title,
                "source":       feed.feed.get("title", "RSS"),
                "url":          entry.get("link", ""),
                "published_at": dt.isoformat() if dt else None,
                "staleness_hrs": round(staleness, 2),
                "decay_weight":  round(_decay(staleness), 4),
            })
        return results
    except Exception as e:
        log.debug(f"RSS fetch failed for {feed_url}: {e}")
        return []

def _fetch_newsapi(query: str):
    if not NEWS_API_KEY:
        return []

    params = {
        "q": query,
        "from": _hours_ago(NEWS_LOOKBACK_HOURS),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": NEWS_API_KEY,
    }

    try:
        r = requests.get(NEWSAPI_BASE, params=params, timeout=5)
        r.raise_for_status()
        return r.json().get("articles", [])
    except Exception as e:
        log.debug(f"NewsAPI error: {e}")
        return []


def _fetch_newsdata(query: str):
    if not NEWSDATA_API_KEY:
        return []

    params = {
        "apikey": NEWSDATA_API_KEY,
        "q": query,
        "country": "in",
        "language": "en",
    }

    try:
        r = requests.get(NEWSDATA_BASE, params=params, timeout=5)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        log.debug(f"NewsData error: {e}")
        return []


def _fetch_marketaux(query: str):
    if not MARKETAUX_API_KEY:
        return []

    params = {
        "api_token": MARKETAUX_API_KEY,
        "search": query,
        "countries": "in",
        "limit": 10,
    }

    try:
        r = requests.get(MARKETAUX_BASE, params=params, timeout=5)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        log.debug(f"Marketaux error: {e}")
        return []


# -------------------- MAIN FUNCTION --------------------

def get_news_for_stock(symbol: str, company_name: str = None):
    cache_key = f"news:{symbol}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    fetched_at = datetime.now(timezone.utc)
    queries = _build_queries(symbol, company_name)

    raw_articles = []

    # Fetch from all sources
    for q in queries:
        raw_articles += _fetch_newsapi(q)
        raw_articles += _fetch_newsdata(q)
        raw_articles += _fetch_marketaux(q)

    # Inside get_news_for_stock(), add RSS sources:
    for feed_name, url in RSS_FEEDS.items():
        raw_articles += _fetch_rss(url, symbol, company_name)

    # Fallback: loose query if nothing found
    if not raw_articles and company_name:
        log.info(f"Fallback to loose query for {symbol}")
        raw_articles += _fetch_newsapi(company_name.split()[0])

    if not raw_articles:
        cache.set_json(cache_key, [], ttl_secs=300)
        return []

    seen = set()
    results = []

    for a in raw_articles:
        headline = (a.get("title") or a.get("headline") or "").strip()
        if not headline:
            continue

        h = _hash(headline)
        if h in seen:
            continue
        seen.add(h)

        published = (
            a.get("publishedAt")
            or a.get("pubDate")
            or a.get("published_at")
        )

        dt = _parse_date(published)
        staleness = _staleness_hours(dt)

        if staleness > NEWS_LOOKBACK_HOURS:
            continue

        results.append({
            "headline": headline,
            "source": str(a.get("source", ""))[:50],
            "url": a.get("link") or a.get("url"),
            "published_at": dt.isoformat() if dt else None,
            "fetched_at": fetched_at.isoformat(),
            "staleness_hrs": round(staleness, 2),
            "decay_weight": round(_decay(staleness), 4),
            "hash": h,
        })

        if len(results) >= MAX_HEADLINES_PER_STOCK:
            break

    cache.set_json(cache_key, results, ttl_secs=CACHE_TTL_NEWS_SECS)
    return results


# -------------------- SENTIMENT INPUT --------------------

def get_aggregated_sentiment_input(symbol: str, company_name: str = None):
    news = get_news_for_stock(symbol, company_name)

    return [
        item["headline"]
        for item in sorted(news, key=lambda x: x["decay_weight"], reverse=True)
        if item["decay_weight"] > 0.2
    ]