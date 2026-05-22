"""
News aggregation module.

Pulls live data from RSS feeds, Yahoo Finance (via yfinance), and SEC EDGAR.
Each source is isolated — a failure skips that source and logs the error
without blocking the full pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests
import yfinance as yf

from config import get_config

logger = logging.getLogger(__name__)
cfg = get_config()

# ---------------------------------------------------------------------------
# RSS feed registry
# ---------------------------------------------------------------------------
RSS_FEEDS: dict[str, str] = {
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "reuters_markets": "https://feeds.reuters.com/reuters/marketsNews",
    "reuters_tech": "https://feeds.reuters.com/reuters/technologyNews",
    "cnbc_markets": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "cnbc_finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "marketwatch_top": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_markets": "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "yahoo_finance": "https://finance.yahoo.com/rss/headline",
    "fed_press": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ft_world": "https://www.ft.com/rss/home/us",
    "wsj_markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "barrons": "https://www.barrons.com/real-time-market-data/feed",
    "seeking_alpha": "https://seekingalpha.com/market_currents.xml",
}

# ---------------------------------------------------------------------------
# Category keyword scoring
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "markets_macro": [
        "federal reserve", "fed ", "fomc", "interest rate", "inflation", "cpi", "ppi",
        "gdp", "employment", "unemployment", "jobs report", "nonfarm", "treasury",
        "yield", "basis points", "rate hike", "rate cut", "powell", "lagarde",
        "central bank", "monetary policy", "recession", "dollar", "euro", "yen",
        "yuan", "fx", "currency", "dxy", "oil price", "crude", "wti", "brent",
        "gold price", "commodity", "supply chain", "trade deficit",
    ],
    "corporate_intelligence": [
        "earnings", "quarterly", "revenue", "profit", "eps", "guidance", "outlook",
        "acquisition", "merger", "m&a", "ipo", "buyback", "dividend", "analyst",
        "upgrade", "downgrade", "price target", "ceo", "cfo", "board", "layoff",
        "restructuring", "beat", "miss", "raised", "lowered", "forecast",
        "sec filing", "8-k", "13-d", "13-f",
    ],
    "tech_ai_watch": [
        "artificial intelligence", " ai ", "openai", "chatgpt", "llm", "large language",
        "nvidia", "semiconductor", "chip", "microsoft", "google", "alphabet", "meta",
        "apple", "amazon", "aws", "cloud computing", "startup", "funding round",
        "series a", "series b", "venture capital", "antitrust", "big tech",
        "regulation", "data privacy", "cybersecurity", "hack", "breach",
    ],
    "risk_radar": [
        "sanction", "tariff", "trade war", "geopolit", "conflict", "war", "iran",
        "russia", "ukraine", "china", "middle east", "taiwan", "north korea",
        "sec ", "ftc", "doj", "lawsuit", "investigation", "penalty", "fine",
        "compliance", "regulatory", "ban", "probe", "subpoena", "indictment",
        "default", "credit downgrade", "sovereign", "systemic risk",
    ],
}

# Market tickers
MARKET_TICKERS: dict[str, str] = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "treasury_10y": "^TNX",
    "dxy": "DX-Y.NYB",
    "wti_crude": "CL=F",
    "gold": "GC=F",
    "btc": "BTC-USD",
}

MARKET_LABELS: dict[str, str] = {
    "sp500": "S&P 500",
    "nasdaq": "NASDAQ",
    "dow": "Dow Jones",
    "treasury_10y": "10Y Treasury",
    "dxy": "DXY",
    "wti_crude": "WTI Crude",
    "gold": "Gold",
    "btc": "Bitcoin",
}

MARKET_FORMATS: dict[str, str] = {
    "sp500": "index",
    "nasdaq": "index",
    "dow": "index",
    "treasury_10y": "yield",
    "dxy": "index",
    "wti_crude": "price",
    "gold": "price",
    "btc": "crypto",
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class NewsAggregator:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; MorningBriefingBot/1.0; "
                "+https://github.com/your-org/daily-briefing)"
            )
        })

    def collect_all(self) -> dict[str, Any]:
        """
        Run the full collection pipeline.

        Returns a dict with keys:
          market_snapshot, headlines, sec_filings, sources_used, sources_failed
        """
        sources_used: list[str] = []
        sources_failed: list[str] = []
        all_headlines: list[dict] = []

        # Market data
        market_snapshot = self._fetch_market_snapshot(sources_used, sources_failed)

        # RSS headlines
        for source_key, url in RSS_FEEDS.items():
            items = self._fetch_rss(source_key, url, sources_failed)
            if items:
                all_headlines.extend(items)
                sources_used.append(source_key)

        # SEC EDGAR recent 8-K filings
        sec_filings = self._fetch_sec_filings(sources_used, sources_failed)

        # Deduplicate and score
        headlines = _deduplicate(all_headlines)
        headlines = _score_and_sort(headlines)

        logger.info(
            "Aggregation complete: %d headlines, %d SEC filings, %d sources used, %d failed.",
            len(headlines), len(sec_filings), len(sources_used), len(sources_failed),
        )

        return {
            "market_snapshot": market_snapshot,
            "headlines": headlines,
            "sec_filings": sec_filings,
            "sources_used": sources_used,
            "sources_failed": sources_failed,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Internal: market data
    # ------------------------------------------------------------------

    def _fetch_market_snapshot(
        self, sources_used: list, sources_failed: list
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}

        for key, ticker_sym in MARKET_TICKERS.items():
            try:
                ticker = yf.Ticker(ticker_sym)
                hist = ticker.history(period="5d")

                if hist.empty or len(hist) < 2:
                    raise ValueError(f"Insufficient history for {ticker_sym}")

                prev_close = float(hist["Close"].iloc[-2])
                current = float(hist["Close"].iloc[-1])
                change_pct = ((current - prev_close) / prev_close) * 100

                snapshot[key] = {
                    "label": MARKET_LABELS[key],
                    "format": MARKET_FORMATS[key],
                    "value": current,
                    "prev_close": prev_close,
                    "change_pct": round(change_pct, 2),
                    "direction": "up" if change_pct >= 0.05 else ("down" if change_pct <= -0.05 else "flat"),
                }
            except Exception as exc:
                logger.warning("Market data failed for %s: %s", ticker_sym, exc)
                snapshot[key] = {
                    "label": MARKET_LABELS[key],
                    "format": MARKET_FORMATS[key],
                    "value": None,
                    "prev_close": None,
                    "change_pct": None,
                    "direction": "flat",
                    "error": str(exc),
                }
                sources_failed.append(f"yfinance:{ticker_sym}")

        sources_used.append("yfinance")
        return snapshot

    # ------------------------------------------------------------------
    # Internal: RSS
    # ------------------------------------------------------------------

    def _fetch_rss(
        self, source_key: str, url: str, sources_failed: list
    ) -> list[dict]:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": self.session.headers["User-Agent"]})

            if feed.bozo and not feed.entries:
                raise ValueError(f"Feed parse error: {feed.bozo_exception}")

            items = []
            for entry in feed.entries[:30]:
                title = _clean_text(entry.get("title", ""))
                summary = _clean_text(entry.get("summary", entry.get("description", "")))
                link = entry.get("link", "")
                published = _parse_date(entry.get("published", entry.get("updated", "")))

                if not title:
                    continue

                items.append({
                    "headline": title,
                    "summary": summary[:500] if summary else "",
                    "url": link,
                    "source": source_key,
                    "published": published,
                    "fingerprint": _fingerprint(title),
                })

            return items

        except Exception as exc:
            logger.warning("RSS fetch failed for %s (%s): %s", source_key, url, exc)
            sources_failed.append(source_key)
            return []

    # ------------------------------------------------------------------
    # Internal: SEC EDGAR
    # ------------------------------------------------------------------

    def _fetch_sec_filings(
        self, sources_used: list, sources_failed: list
    ) -> list[dict]:
        try:
            url = (
                "https://efts.sec.gov/LATEST/search-index?"
                "q=%228-K%22&dateRange=custom"
                f"&startdt={datetime.now().strftime('%Y-%m-%d')}"
                f"&enddt={datetime.now().strftime('%Y-%m-%d')}"
                "&forms=8-K"
                "&hits.hits._source=period_of_report,entity_name,file_date,form_type"
                "&hits.hits.total.value=true"
                "&hits.hits.hits.total.value=true"
            )
            resp = self.session.get(url, timeout=cfg.project_root and 15 or 15)
            resp.raise_for_status()
            data = resp.json()

            filings = []
            for hit in (data.get("hits", {}).get("hits", []) or [])[:20]:
                src = hit.get("_source", {})
                filings.append({
                    "entity": src.get("entity_name", "Unknown"),
                    "form_type": src.get("form_type", "8-K"),
                    "file_date": src.get("file_date", ""),
                    "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={src.get('entity_name','')}&type=8-K&dateb=&owner=include&count=10",
                })

            sources_used.append("sec_edgar")
            return filings

        except Exception as exc:
            logger.warning("SEC EDGAR fetch failed: %s", exc)
            sources_failed.append("sec_edgar")
            return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_date(date_str: str) -> str:
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        import email.utils
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.isoformat()
    except Exception:
        return date_str


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())[:80]
    return hashlib.md5(normalized.encode()).hexdigest()


def _deduplicate(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in items:
        fp = item.get("fingerprint", _fingerprint(item.get("headline", "")))
        if fp not in seen:
            seen.add(fp)
            unique.append(item)
    return unique


def _score_item(item: dict) -> float:
    text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
    score = 0.0

    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                score += 1.0

    # Recency bonus: items from last 4 hours get +5
    try:
        pub_str = item.get("published", "")
        if pub_str:
            import email.utils
            pub_tuple = email.utils.parsedate_to_datetime(pub_str)
            age_hours = (datetime.now(timezone.utc) - pub_tuple).total_seconds() / 3600
            if age_hours <= 4:
                score += 5.0
    except Exception:
        pass

    return score


def _score_and_sort(items: list[dict]) -> list[dict]:
    for item in items:
        item["relevance_score"] = _score_item(item)
    return sorted(items, key=lambda x: x.get("relevance_score", 0), reverse=True)
