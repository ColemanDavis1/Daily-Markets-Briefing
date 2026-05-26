"""
News aggregation module.

Data sources:
  - Stooq       : market prices (no API key)
  - Finnhub     : financial news, earnings calendar, economic calendar (free key)
  - FRED        : Federal Reserve macro data series (free key)
  - NewsAPI     : keyword-targeted news per section (free key)
  - RSS feeds   : Reuters, CNBC, MarketWatch, FT, WSJ, Fed press releases
  - SEC EDGAR   : overnight 8-K filings

Each source fails independently — a broken source skips and logs without
blocking the rest of the pipeline.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests

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
    "reuters_health": "https://feeds.reuters.com/reuters/healthNews",
    "cnbc_markets": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "cnbc_finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "cnbc_tech": "https://www.cnbc.com/id/19854910/device/rss/rss.html",
    "marketwatch_top": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_markets": "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "yahoo_finance": "https://finance.yahoo.com/rss/headline",
    "fed_press": "https://www.federalreserve.gov/feeds/press_all.xml",
    "wsj_markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "ft_world": "https://www.ft.com/rss/home/us",
    "seeking_alpha": "https://seekingalpha.com/market_currents.xml",
}

# ---------------------------------------------------------------------------
# Section definitions — keywords route headlines to sections
# ---------------------------------------------------------------------------
SECTIONS = [
    "markets_macro",
    "corporate_earnings",
    "technology_ai",
    "healthcare",
    "industrials",
    "energy_commodities",
    "geopolitical_risk",
    "economic_data",
]

SECTION_KEYWORDS: dict[str, list[str]] = {
    "markets_macro": [
        "s&p 500", "nasdaq", "dow jones", "stock market", "wall street", "equity",
        "bond market", "treasury yield", "10-year", "yield curve", "fed funds",
        "futures", "market rally", "market selloff", "bear market", "bull market",
        "volatility", "vix", "options", "hedge fund", "asset management",
        "dollar index", "dxy", "forex", "currency", "euro", "yen", "pound",
    ],
    "corporate_earnings": [
        "earnings", "quarterly results", "revenue", "profit", "eps", "guidance",
        "outlook", "beat expectations", "missed estimates", "acquisition",
        "merger", "m&a", "buyout", "ipo", "initial public offering", "buyback",
        "dividend", "analyst upgrade", "analyst downgrade", "price target",
        "ceo", "chief executive", "board of directors", "layoff", "restructuring",
        "sec filing", "8-k", "13-d", "annual report",
    ],
    "technology_ai": [
        "artificial intelligence", " ai ", "machine learning", "openai", "chatgpt",
        "large language model", "llm", "nvidia", "semiconductor", "chip",
        "microsoft", "google", "alphabet", "meta", "apple", "amazon", "aws",
        "cloud computing", "data center", "startup", "funding round", "series",
        "venture capital", "antitrust tech", "big tech", "cybersecurity",
        "data breach", "software", "saas", "autonomous", "robotics",
    ],
    "healthcare": [
        "healthcare", "pharmaceutical", "pharma", "biotech", "biotechnology",
        "fda approval", "fda rejection", "clinical trial", "drug approval",
        "hospital", "health insurance", "medicare", "medicaid", "unitedhealth",
        "johnson & johnson", "pfizer", "eli lilly", "abbvie", "merck",
        "vaccine", "therapy", "oncology", "rare disease", "biosimilar",
        "cms", "affordable care act", "health system", "medical device",
    ],
    "industrials": [
        "manufacturing", "industrial", "factory", "production", "aerospace",
        "defense contract", "caterpillar", "boeing", "general electric", "ge ",
        "honeywell", "3m", "raytheon", "lockheed", "northrop", "ups", "fedex",
        "logistics", "freight", "railroad", "infrastructure", "construction",
        "automation", "supply chain", "inventory", "pmi", "purchasing managers",
        "heavy equipment", "machinery", "union", "labor contract",
    ],
    "energy_commodities": [
        "oil price", "crude oil", "wti", "brent", "opec", "natural gas",
        "lng", "gasoline", "refinery", "exxon", "chevron", "conocophillips",
        "schlumberger", "halliburton", "pipeline", "renewable energy", "solar",
        "wind energy", "battery", "lithium", "copper", "iron ore", "steel",
        "aluminum", "gold price", "silver", "commodity", "energy sector",
        "drilling", "shale", "offshore", "carbon", "emissions trading",
    ],
    "geopolitical_risk": [
        "sanction", "tariff", "trade war", "trade dispute", "geopolit",
        "conflict", "war", "military", "iran", "russia", "ukraine", "china",
        "middle east", "taiwan strait", "north korea", "israel", "nato",
        "g7", "g20", "imf", "world bank", "emerging market", "sovereign debt",
        "election", "political risk", "coup", "protest", "regime",
        "sec investigation", "ftc", "doj", "antitrust", "regulatory action",
    ],
    "economic_data": [
        "inflation", "consumer price", "cpi", "pce", "producer price", "ppi",
        "federal reserve", "fomc", "powell", "interest rate", "rate hike",
        "rate cut", "monetary policy", "quantitative", "balance sheet",
        "unemployment rate", "jobs report", "nonfarm payroll", "jobless claims",
        "gdp", "gross domestic product", "recession", "economic growth",
        "consumer confidence", "retail sales", "housing starts", "existing home",
        "trade balance", "current account", "personal income", "spending",
    ],
}

# Finnhub news categories
FINNHUB_CATEGORIES = ["general", "merger", "forex", "crypto"]

# NewsAPI targeted queries per section
NEWSAPI_QUERIES: dict[str, str] = {
    "healthcare": "healthcare pharmaceutical biotech FDA drug approval",
    "industrials": "manufacturing industrial aerospace defense infrastructure",
    "geopolitical_risk": "sanctions tariffs geopolitical trade war conflict",
    "economic_data": "Federal Reserve inflation CPI GDP unemployment",
    "technology_ai": "artificial intelligence semiconductor technology regulation",
    "energy_commodities": "oil crude OPEC energy commodity natural gas",
}

# FRED series to pull
FRED_SERIES: dict[str, str] = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "unemployment": "UNRATE",
    "gdp_growth": "GDP",
    "yield_spread_10y2y": "T10Y2Y",
    "mortgage_30y": "MORTGAGE30US",
}

# Tickers: (stooq_symbol, yahoo_symbol, label, format)
MARKET_TICKERS: dict[str, tuple[str, str, str, str]] = {
    "sp500":       ("^spx",   "^GSPC",    "S&P 500",      "index"),
    "nasdaq":      ("^ndq",   "^NDX",     "NASDAQ 100",   "index"),
    "dow":         ("^dji",   "^DJI",     "Dow Jones",    "index"),
    "russell2000": ("^rut",   "^RUT",     "Russell 2000", "index"),
    "vix":         ("^vix",   "^VIX",     "VIX",          "index"),
    "treasury_10y":("10us.b", "^TNX",     "10Y Treasury", "yield"),
    "wti_crude":   ("cl.f",   "CL=F",     "WTI Crude",    "price"),
    "gold":        ("xauusd", "GC=F",     "Gold",         "price"),
    "eurusd":      ("eurusd", "EURUSD=X", "EUR/USD",      "fx"),
    "btc":         ("btcusd", "BTC-USD",  "Bitcoin",      "crypto"),
}

CATEGORY_KEYWORDS = SECTION_KEYWORDS  # alias used by ai_synthesizer


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class NewsAggregator:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })

    def collect_all(self) -> dict[str, Any]:
        sources_used: list[str] = []
        sources_failed: list[str] = []
        all_headlines: list[dict] = []

        # --- Market prices ---
        market_snapshot = self._fetch_market_snapshot(sources_used, sources_failed)

        # --- FRED macro data ---
        macro_data = self._fetch_fred_data(sources_used, sources_failed)

        # --- Finnhub news + calendars ---
        earnings_calendar: list[dict] = []
        economic_calendar: list[dict] = []
        if cfg.finnhub_api_key:
            for cat in FINNHUB_CATEGORIES:
                items = self._fetch_finnhub_news(cat, sources_failed)
                all_headlines.extend(items)
            if items:
                sources_used.append("finnhub")
            earnings_calendar = self._fetch_finnhub_earnings(sources_used, sources_failed)
            economic_calendar = self._fetch_finnhub_economic_calendar(sources_used, sources_failed)
        else:
            logger.info("FINNHUB_API_KEY not set — skipping Finnhub.")

        # --- RSS feeds ---
        for source_key, url in RSS_FEEDS.items():
            items = self._fetch_rss(source_key, url, sources_failed)
            if items:
                all_headlines.extend(items)
                sources_used.append(source_key)

        # --- NewsAPI per-section ---
        newsapi_headlines: list[dict] = []
        if cfg.news_api_key:
            for section, query in NEWSAPI_QUERIES.items():
                items = self._fetch_newsapi(query, section, sources_failed)
                newsapi_headlines.extend(items)
            if newsapi_headlines:
                all_headlines.extend(newsapi_headlines)
                sources_used.append("newsapi")
        else:
            logger.info("NEWS_API_KEY not set — skipping NewsAPI.")

        # --- SEC EDGAR ---
        sec_filings = self._fetch_sec_filings(sources_used, sources_failed)

        # --- Route headlines to sections ---
        all_headlines = _deduplicate(all_headlines)
        sections = _route_to_sections(all_headlines)

        # Add earnings to corporate section
        if earnings_calendar:
            sections["corporate_earnings"] = (
                sections.get("corporate_earnings", []) +
                _earnings_to_headlines(earnings_calendar)
            )

        logger.info(
            "Aggregation complete. Total headlines: %d across %d sections. "
            "Sources used: %d, failed: %d.",
            sum(len(v) for v in sections.values()),
            len(sections),
            len(sources_used),
            len(sources_failed),
        )

        return {
            "market_snapshot": market_snapshot,
            "macro_data": macro_data,
            "sections": sections,
            "earnings_calendar": earnings_calendar,
            "economic_calendar": economic_calendar,
            "sec_filings": sec_filings,
            "sources_used": list(set(sources_used)),
            "sources_failed": list(set(sources_failed)),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Stooq market data
    # ------------------------------------------------------------------

    def _fetch_market_snapshot(
        self, sources_used: list, sources_failed: list
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        any_success = False

        end_date = datetime.now()
        start_date = end_date - timedelta(days=14)
        d1 = start_date.strftime("%Y%m%d")
        d2 = end_date.strftime("%Y%m%d")

        for key, (stooq_sym, yahoo_sym, label, fmt) in MARKET_TICKERS.items():
            data = self._fetch_stooq_quote(stooq_sym, label, fmt, d1, d2)
            if data is None:
                logger.info("Stooq failed for %s, trying Yahoo Finance.", stooq_sym)
                data = self._fetch_yahoo_quote(yahoo_sym, label, fmt)
            if data is not None:
                snapshot[key] = data
                any_success = True
            else:
                snapshot[key] = {
                    "label": label, "format": fmt,
                    "value": None, "change_pct": None, "direction": "flat",
                }
                sources_failed.append(f"market:{key}")

        if any_success:
            sources_used.append("market_data")
        return snapshot

    def _fetch_stooq_quote(
        self, symbol: str, label: str, fmt: str, d1: str, d2: str
    ) -> dict | None:
        try:
            url = f"https://stooq.com/q/d/l/?s={symbol}&d1={d1}&d2={d2}&i=d"
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            rows = [
                r for r in csv.DictReader(io.StringIO(resp.text))
                if r.get("Close", "N/A") not in ("N/A", "")
            ]
            if len(rows) < 2:
                return None
            prev_close = float(rows[-2]["Close"])
            current = float(rows[-1]["Close"])
            change_pct = ((current - prev_close) / prev_close) * 100
            return {
                "label": label, "format": fmt,
                "value": current, "prev_close": prev_close,
                "change_pct": round(change_pct, 2),
                "direction": "up" if change_pct >= 0.05 else "down" if change_pct <= -0.05 else "flat",
            }
        except Exception as exc:
            logger.warning("Stooq failed for %s: %s", symbol, exc)
            return None

    def _fetch_yahoo_quote(self, symbol: str, label: str, fmt: str) -> dict | None:
        try:
            resp = self.session.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "10d"},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()["chart"]["result"][0]
            closes = result["indicators"]["quote"][0]["close"]
            valid = [v for v in closes if v is not None]
            if len(valid) < 2:
                return None
            prev_close, current = valid[-2], valid[-1]
            change_pct = ((current - prev_close) / prev_close) * 100
            return {
                "label": label, "format": fmt,
                "value": current, "prev_close": prev_close,
                "change_pct": round(change_pct, 2),
                "direction": "up" if change_pct >= 0.05 else "down" if change_pct <= -0.05 else "flat",
            }
        except Exception as exc:
            logger.warning("Yahoo Finance failed for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # FRED macro data
    # ------------------------------------------------------------------

    def _fetch_fred_data(
        self, sources_used: list, sources_failed: list
    ) -> dict[str, Any]:
        if not cfg.fred_api_key:
            logger.info("FRED_API_KEY not set — skipping FRED.")
            return {}

        macro: dict[str, Any] = {}
        base = "https://api.stlouisfed.org/fred/series/observations"

        for name, series_id in FRED_SERIES.items():
            try:
                resp = self.session.get(
                    base,
                    params={
                        "series_id": series_id,
                        "api_key": cfg.fred_api_key,
                        "limit": 2,
                        "sort_order": "desc",
                        "file_type": "json",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                obs = resp.json().get("observations", [])
                if obs:
                    latest = obs[0]
                    prev = obs[1] if len(obs) > 1 else None
                    val = latest.get("value", ".")
                    macro[name] = {
                        "value": float(val) if val != "." else None,
                        "date": latest.get("date", ""),
                        "prev_value": float(prev["value"]) if prev and prev.get("value", ".") != "." else None,
                    }
            except Exception as exc:
                logger.warning("FRED fetch failed for %s: %s", series_id, exc)
                sources_failed.append(f"fred:{series_id}")

        if macro:
            sources_used.append("fred")
        return macro

    # ------------------------------------------------------------------
    # Finnhub
    # ------------------------------------------------------------------

    def _fetch_finnhub_news(
        self, category: str, sources_failed: list
    ) -> list[dict]:
        try:
            resp = self.session.get(
                "https://finnhub.io/api/v1/news",
                params={"category": category, "token": cfg.finnhub_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            items = []
            for art in resp.json()[:40]:
                headline = _clean_text(art.get("headline", ""))
                summary = _clean_text(art.get("summary", ""))
                if not headline:
                    continue
                published = datetime.fromtimestamp(
                    art.get("datetime", 0), tz=timezone.utc
                ).isoformat()
                if not _is_recent(published):
                    continue
                items.append({
                    "headline": headline,
                    "summary": summary[:500],
                    "url": art.get("url", ""),
                    "source": f"finnhub:{category}",
                    "published": published,
                    "fingerprint": _fingerprint(headline),
                })
            return items
        except Exception as exc:
            logger.warning("Finnhub news failed (%s): %s", category, exc)
            sources_failed.append(f"finnhub:{category}")
            return []

    def _fetch_finnhub_earnings(
        self, sources_used: list, sources_failed: list
    ) -> list[dict]:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            week_out = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            resp = self.session.get(
                "https://finnhub.io/api/v1/calendar/earnings",
                params={"from": today, "to": week_out, "token": cfg.finnhub_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            earnings = data.get("earningsCalendar", [])[:30]
            sources_used.append("finnhub_earnings")
            return earnings
        except Exception as exc:
            logger.warning("Finnhub earnings calendar failed: %s", exc)
            sources_failed.append("finnhub_earnings")
            return []

    def _fetch_finnhub_economic_calendar(
        self, sources_used: list, sources_failed: list
    ) -> list[dict]:
        try:
            resp = self.session.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"token": cfg.finnhub_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            events = data.get("economicCalendar", [])[:20]
            sources_used.append("finnhub_economic_calendar")
            return events
        except Exception as exc:
            logger.warning("Finnhub economic calendar failed: %s", exc)
            sources_failed.append("finnhub_economic_calendar")
            return []

    # ------------------------------------------------------------------
    # NewsAPI
    # ------------------------------------------------------------------

    def _fetch_newsapi(
        self, query: str, section: str, sources_failed: list
    ) -> list[dict]:
        try:
            resp = self.session.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 15,
                    "apiKey": cfg.news_api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
            items = []
            for art in articles:
                headline = _clean_text(art.get("title", ""))
                summary = _clean_text(art.get("description", ""))
                if not headline or "[Removed]" in headline:
                    continue
                items.append({
                    "headline": headline,
                    "summary": summary[:500],
                    "url": art.get("url", ""),
                    "source": f"newsapi:{art.get('source', {}).get('name', '')}",
                    "published": art.get("publishedAt", ""),
                    "fingerprint": _fingerprint(headline),
                    "section_hint": section,
                })
            return items
        except Exception as exc:
            logger.warning("NewsAPI failed for '%s': %s", query, exc)
            sources_failed.append(f"newsapi:{section}")
            return []

    # ------------------------------------------------------------------
    # RSS feeds
    # ------------------------------------------------------------------

    def _fetch_rss(
        self, source_key: str, url: str, sources_failed: list
    ) -> list[dict]:
        try:
            feed = feedparser.parse(
                url,
                request_headers={"User-Agent": self.session.headers["User-Agent"]},
            )
            if feed.bozo and not feed.entries:
                raise ValueError(f"Feed parse error: {feed.bozo_exception}")

            items = []
            for entry in feed.entries[:25]:
                title = _clean_text(entry.get("title", ""))
                summary = _clean_text(
                    entry.get("summary", entry.get("description", ""))
                )
                if not title:
                    continue
                published = _parse_date(
                    entry.get("published", entry.get("updated", ""))
                )
                if not _is_recent(published):
                    continue
                items.append({
                    "headline": title,
                    "summary": summary[:500],
                    "url": entry.get("link", ""),
                    "source": source_key,
                    "published": published,
                    "fingerprint": _fingerprint(title),
                })
            return items
        except Exception as exc:
            logger.warning("RSS failed for %s: %s", source_key, exc)
            sources_failed.append(source_key)
            return []

    # ------------------------------------------------------------------
    # SEC EDGAR
    # ------------------------------------------------------------------

    def _fetch_sec_filings(
        self, sources_used: list, sources_failed: list
    ) -> list[dict]:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            resp = self.session.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": "8-K",
                    "dateRange": "custom",
                    "startdt": today,
                    "enddt": today,
                    "forms": "8-K",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            filings = []
            for hit in (data.get("hits", {}).get("hits", []) or [])[:15]:
                src = hit.get("_source", {})
                filings.append({
                    "entity": src.get("entity_name", "Unknown"),
                    "form_type": src.get("form_type", "8-K"),
                    "file_date": src.get("file_date", ""),
                })
            sources_used.append("sec_edgar")
            return filings
        except Exception as exc:
            logger.warning("SEC EDGAR failed: %s", exc)
            sources_failed.append("sec_edgar")
            return []


# ---------------------------------------------------------------------------
# Routing and helpers
# ---------------------------------------------------------------------------

def _route_to_sections(headlines: list[dict]) -> dict[str, list[dict]]:
    """Assign each headline to its best-matching section."""
    sections: dict[str, list[dict]] = {s: [] for s in SECTIONS}

    for item in headlines:
        # If NewsAPI already tagged it, respect that hint
        hint = item.get("section_hint")
        if hint and hint in sections:
            sections[hint].append(item)
            continue

        text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        best_section = "markets_macro"
        best_score = 0.0

        for section, keywords in SECTION_KEYWORDS.items():
            score = sum(1.0 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_section = section

        sections[best_section].append(item)

    # Sort each section by recency (published date, newest first), cap at 25
    for section in sections:
        sections[section] = sorted(
            sections[section],
            key=lambda x: x.get("published", ""),
            reverse=True,
        )[:25]

    return sections


def _earnings_to_headlines(earnings: list[dict]) -> list[dict]:
    items = []
    for e in earnings:
        symbol = e.get("symbol", "")
        date = e.get("date", "today")
        eps_est = e.get("epsEstimate")
        est_str = f" (EPS est: ${eps_est:.2f})" if eps_est else ""
        headline = f"{symbol} reports earnings {date}{est_str}"
        items.append({
            "headline": headline,
            "summary": f"Earnings release scheduled for {date}. " + (
                f"Consensus EPS estimate: ${eps_est:.2f}." if eps_est else ""
            ),
            "source": "finnhub_earnings",
            "published": datetime.now(timezone.utc).isoformat(),
            "fingerprint": _fingerprint(headline),
        })
    return items


def _is_recent(published_iso: str, max_hours: int = 48) -> bool:
    """Return True if the article was published within the last max_hours hours."""
    try:
        from datetime import datetime as _dt
        pub = _dt.fromisoformat(published_iso)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - pub).total_seconds() < max_hours * 3600
    except Exception:
        return True  # if unparseable, include it


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text).replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_date(date_str: str) -> str:
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        import email.utils
        return email.utils.parsedate_to_datetime(date_str).isoformat()
    except Exception:
        return date_str


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())[:80]
    return hashlib.md5(normalized.encode()).hexdigest()


def _deduplicate(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in items:
        fp = item.get("fingerprint") or _fingerprint(item.get("headline", ""))
        if fp not in seen:
            seen.add(fp)
            unique.append(item)
    return unique
