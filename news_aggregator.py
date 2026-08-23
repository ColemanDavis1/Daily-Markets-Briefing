"""
News and market data aggregation.

Data sources:
  Yahoo       prices for indices, futures, sectors, FX and commodities
  Stooq       fallback only; it now serves an HTML block page for most symbols
  FRED        rates, funding, inflation, credit spreads, macro (free key)
  Finnhub     financial news, earnings calendar, economic calendar (free key)
  NewsAPI     keyword-targeted news per group (free key, optional)
  RSS         CNBC, NYT DealBook, Yahoo Finance, MarketWatch, Seeking Alpha,
              Investing.com, Guardian, BBC, Federal Reserve
  SEC EDGAR   latest 8-K filings via the official current-filings feed

Every source fails independently. A broken source logs and skips without
blocking the rest of the pipeline.

Headlines are routed to the twelve investment banking groups defined in
groups.py, with product-group keywords weighted above coverage-group keywords
so a deal story lands on the desk that would execute it.
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
from groups import (
    ALL_GROUPS,
    COVERAGE_KEYWORD_WEIGHT,
    MIN_ROUTING_SCORE,
    PRODUCT_KEYWORD_WEIGHT,
    ROUTED_GROUPS,
)

logger = logging.getLogger(__name__)
cfg = get_config()

# SEC requires a descriptive User-Agent with contact information.
_SEC_USER_AGENT = "Daily-News-Briefing/2.0 (personal research; contact via repository owner)"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# RSS feed registry
# ---------------------------------------------------------------------------
# Verified live as of the last feed audit. Reuters (feeds.reuters.com), FT and
# Treasury have retired their public RSS; WSJ and MarketWatch marketpulse still
# respond but serve frozen 2025 content. All five were removed rather than left
# to fail silently every morning. Re-audit with tools/feed_check.py.
RSS_FEEDS: dict[str, str] = {
    # CNBC verticals
    "cnbc_markets":     "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "cnbc_finance":     "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "cnbc_deals":       "https://www.cnbc.com/id/10000115/device/rss/rss.html",
    "cnbc_tech":        "https://www.cnbc.com/id/19854910/device/rss/rss.html",
    "cnbc_earnings":    "https://www.cnbc.com/id/15839135/device/rss/rss.html",
    "cnbc_energy":      "https://www.cnbc.com/id/19836768/device/rss/rss.html",
    "cnbc_economy":     "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "cnbc_healthcare":  "https://www.cnbc.com/id/10000108/device/rss/rss.html",
    "cnbc_retail":      "https://www.cnbc.com/id/10000116/device/rss/rss.html",
    # New York Times. DealBook is the single best free M&A feed available.
    "nyt_dealbook":     "https://rss.nytimes.com/services/xml/rss/nyt/DealBook.xml",
    "nyt_business":     "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "nyt_economy":      "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
    # Aggregators and wires
    "yahoo_finance":    "https://finance.yahoo.com/news/rssindex",
    "marketwatch_top":  "https://feeds.marketwatch.com/marketwatch/topstories/",
    "seekingalpha_mc":  "https://seekingalpha.com/market_currents.xml",
    "investing_news":   "https://www.investing.com/rss/news_25.rss",
    "guardian_business": "https://www.theguardian.com/uk/business/rss",
    "bbc_business":     "http://feeds.bbci.co.uk/news/business/rss.xml",
    # Official
    "fed_press":        "https://www.federalreserve.gov/feeds/press_all.xml",
}

# Human-readable publisher names for citations
SOURCE_NAMES: dict[str, str] = {
    "cnbc": "CNBC",
    "nyt": "New York Times",
    "yahoo": "Yahoo Finance",
    "marketwatch": "MarketWatch",
    "seekingalpha": "Seeking Alpha",
    "investing": "Investing.com",
    "guardian": "The Guardian",
    "bbc": "BBC",
    "fed": "Federal Reserve",
    "finnhub": "Finnhub",
    "newsapi": "NewsAPI",
    "sec": "SEC EDGAR",
}

FINNHUB_CATEGORIES = ["general", "merger", "forex"]

# NewsAPI targeted queries per group (optional source, disabled without a key)
NEWSAPI_QUERIES: dict[str, str] = {
    "ma": "merger OR acquisition OR takeover OR \"definitive agreement\" OR divestiture",
    "ecm": "IPO OR \"initial public offering\" OR \"follow-on offering\" OR \"block trade\"",
    "levfin": "\"high yield\" OR \"leveraged loan\" OR \"private credit\" OR \"term loan\"",
    "sponsors": "\"private equity\" OR \"take-private\" OR buyout OR \"continuation fund\"",
    "restructuring": "\"chapter 11\" OR bankruptcy OR restructuring OR distressed",
    "dcm": "\"bond offering\" OR \"senior notes\" OR \"credit rating\" OR refinancing",
    "healthcare": "FDA OR biotech OR pharmaceutical OR \"clinical trial\"",
    "tmt": "semiconductor OR \"artificial intelligence\" OR software OR \"data center\"",
    "energy_power": "OPEC OR crude OR \"natural gas\" OR utility OR renewable",
    "consumer": "retail sales OR \"same-store sales\" OR consumer spending",
    "industrials": "manufacturing OR aerospace OR \"defense contract\" OR freight",
    "fig": "bank earnings OR \"net interest margin\" OR insurer OR fintech",
    "geopolitical": "tariffs OR sanctions OR \"export controls\" OR \"trade deal\"",
}

# ---------------------------------------------------------------------------
# FRED series registry
#
# transform:
#   level          use the observation as reported
#   yoy_from_index compute year-over-year percent change from an index level
#   diff           month-over-month change in the level (payrolls, in thousands)
#
# percentile: compute 1-year min / max / percentile context for this series
# ---------------------------------------------------------------------------
FRED_SERIES: dict[str, dict] = {
    # Treasury curve
    "ust_3m":            {"id": "DGS3MO", "transform": "level", "obs": 30},
    "ust_6m":            {"id": "DGS6MO", "transform": "level", "obs": 30},
    "ust_1y":            {"id": "DGS1",   "transform": "level", "obs": 30},
    "ust_2y":            {"id": "DGS2",   "transform": "level", "obs": 30},
    "ust_5y":            {"id": "DGS5",   "transform": "level", "obs": 30},
    "ust_10y":           {"id": "DGS10",  "transform": "level", "obs": 30},
    "ust_30y":           {"id": "DGS30",  "transform": "level", "obs": 30},
    # Funding
    "sofr":              {"id": "SOFR",           "transform": "level", "obs": 30},
    "sofr_30day_avg":    {"id": "SOFR30DAYAVG",   "transform": "level", "obs": 30},
    "effr":              {"id": "EFFR",           "transform": "level", "obs": 30},
    "iorb":              {"id": "IORB",            "transform": "level", "obs": 30},
    "reverse_repo":      {"id": "RRPONTSYD",       "transform": "level", "obs": 60},
    "fed_target_upper":  {"id": "DFEDTARU",        "transform": "level", "obs": 30},
    "fed_target_lower":  {"id": "DFEDTARL",        "transform": "level", "obs": 30},
    "fed_funds_rate":    {"id": "FEDFUNDS",        "transform": "level", "obs": 24},
    # Inflation expectations and real yields
    "breakeven_5y":      {"id": "T5YIE",  "transform": "level", "obs": 30},
    "breakeven_10y":     {"id": "T10YIE", "transform": "level", "obs": 30},
    "real_10y":          {"id": "DFII10", "transform": "level", "obs": 30},
    # Credit spreads (percent; multiply by 100 for basis points)
    "hy_oas":            {"id": "BAMLH0A0HYM2", "transform": "level", "obs": 300, "percentile": True},
    "ig_oas":            {"id": "BAMLC0A0CM",   "transform": "level", "obs": 300, "percentile": True},
    # Inflation levels
    "cpi_yoy_rate":      {"id": "CPIAUCSL", "transform": "yoy_from_index", "obs": 20},
    "core_cpi_yoy_rate": {"id": "CPILFESL", "transform": "yoy_from_index", "obs": 20},
    "core_pce_yoy_rate": {"id": "PCEPILFE", "transform": "yoy_from_index", "obs": 20},
    # Labor and growth
    "unemployment":      {"id": "UNRATE", "transform": "level", "obs": 24},
    "payrolls_change":   {"id": "PAYEMS", "transform": "diff",  "obs": 24},
    "real_gdp_growth":   {"id": "A191RL1Q225SBEA", "transform": "level", "obs": 12},
    "retail_sales_yoy":     {"id": "RSXFS",  "transform": "yoy_from_index", "obs": 20},
    "industrial_prod_yoy":  {"id": "INDPRO", "transform": "yoy_from_index", "obs": 20},
    # Housing transmission
    "mortgage_30y":      {"id": "MORTGAGE30US", "transform": "level", "obs": 30},
    "housing_starts":    {"id": "HOUST",        "transform": "level", "obs": 24},
}

# ---------------------------------------------------------------------------
# Market tickers: (stooq_symbol, yahoo_symbol, label, format)
# ---------------------------------------------------------------------------
TICKER_GROUPS: dict[str, dict[str, tuple[str, str, str, str]]] = {
    "primary": {
        "sp500":       ("^spx", "^GSPC", "S&P 500",      "index"),
        "nasdaq":      ("^ndq", "^NDX",  "NASDAQ 100",   "index"),
        "dow":         ("^dji", "^DJI",  "Dow Jones",    "index"),
        "russell2000": ("^rut", "^RUT",  "Russell 2000", "index"),
        "vix":         ("^vix", "^VIX",  "VIX",          "index"),
    },
    # Futures carry the genuine overnight move, which matters for a pre-market send.
    "futures": {
        "es_future": ("es.f", "ES=F", "S&P 500 futures",   "index"),
        "nq_future": ("nq.f", "NQ=F", "NASDAQ futures",    "index"),
        "ym_future": ("ym.f", "YM=F", "Dow futures",       "index"),
    },
    "sectors": {
        "xlk":  ("xlk.us",  "XLK",  "Technology",     "index"),
        "xlf":  ("xlf.us",  "XLF",  "Financials",     "index"),
        "xle":  ("xle.us",  "XLE",  "Energy",         "index"),
        "xlv":  ("xlv.us",  "XLV",  "Health Care",    "index"),
        "xli":  ("xli.us",  "XLI",  "Industrials",    "index"),
        "xlb":  ("xlb.us",  "XLB",  "Materials",      "index"),
        "xlp":  ("xlp.us",  "XLP",  "Cons. Staples",  "index"),
        "xly":  ("xly.us",  "XLY",  "Cons. Discret.", "index"),
        "xlu":  ("xlu.us",  "XLU",  "Utilities",      "index"),
        "xlre": ("xlre.us", "XLRE", "Real Estate",    "index"),
        "xlc":  ("xlc.us",  "XLC",  "Comm. Services", "index"),
    },
    "fx": {
        "dxy":    ("dxy",    "DX-Y.NYB", "Dollar index", "index"),
        "eurusd": ("eurusd", "EURUSD=X", "EUR/USD",      "fx"),
        "gbpusd": ("gbpusd", "GBPUSD=X", "GBP/USD",      "fx"),
        "usdjpy": ("usdjpy", "JPY=X",    "USD/JPY",      "fx"),
    },
    "commodities": {
        "wti_crude": ("cl.f",   "CL=F", "WTI Crude", "price"),
        "brent":     ("bz.f",   "BZ=F", "Brent",     "price"),
        "gold":      ("xauusd", "GC=F", "Gold",      "price"),
        "silver":    ("xagusd", "SI=F", "Silver",    "price"),
        "copper":    ("hg.f",   "HG=F", "Copper",    "price"),
        "nat_gas":   ("ng.f",   "NG=F", "Nat. Gas",  "price"),
    },
    "international": {
        "stoxx600":  ("^stoxx", "^STOXX", "STOXX 600",  "index"),
        "dax":       ("^dax",   "^GDAXI", "DAX",        "index"),
        "ftse100":   ("^ftx",   "^FTSE",  "FTSE 100",   "index"),
        "nikkei":    ("^nkx",   "^N225",  "Nikkei 225", "index"),
        "hang_seng": ("^hsi",   "^HSI",   "Hang Seng",  "index"),
        "shanghai":  ("^shc",   "000001.SS", "Shanghai Comp", "index"),
    },
    "crypto": {
        "btc": ("btcusd", "BTC-USD", "Bitcoin",  "crypto"),
        "eth": ("ethusd", "ETH-USD", "Ethereum", "crypto"),
    },
}

MARKET_TICKERS = {k: v for g in TICKER_GROUPS.values() for k, v in g.items()}

# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

class NewsAggregator:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _BROWSER_UA})

    def collect_all(self) -> dict[str, Any]:
        sources_used: list[str] = []
        sources_failed: list[str] = []
        all_headlines: list[dict] = []

        market_snapshot = self._fetch_market_snapshot(sources_used, sources_failed)
        macro_data = self._fetch_fred_data(sources_used, sources_failed)

        earnings_calendar: list[dict] = []
        economic_calendar: list[dict] = []
        if cfg.finnhub_api_key:
            finnhub_items: list[dict] = []
            for cat in FINNHUB_CATEGORIES:
                finnhub_items.extend(self._fetch_finnhub_news(cat, sources_failed))
            if finnhub_items:
                all_headlines.extend(finnhub_items)
                sources_used.append("finnhub")
            earnings_calendar = self._fetch_finnhub_earnings(sources_used, sources_failed)
            economic_calendar = self._fetch_finnhub_economic_calendar(sources_used, sources_failed)
        else:
            logger.info("FINNHUB_API_KEY not set. Skipping Finnhub.")

        # Finnhub's calendar is premium-only, so fall back to FRED's official
        # release schedule. It has no consensus figures but it is authoritative
        # on what publishes when.
        if not economic_calendar:
            economic_calendar = self._fetch_fred_release_dates(sources_used, sources_failed)

        for source_key, url in RSS_FEEDS.items():
            items = self._fetch_rss(source_key, url, sources_failed)
            if items:
                all_headlines.extend(items)
                sources_used.append(source_key)

        if cfg.news_api_key:
            newsapi_headlines: list[dict] = []
            for group_key, query in NEWSAPI_QUERIES.items():
                newsapi_headlines.extend(
                    self._fetch_newsapi(query, group_key, sources_failed)
                )
            if newsapi_headlines:
                all_headlines.extend(newsapi_headlines)
                sources_used.append("newsapi")
        else:
            logger.info("NEWS_API_KEY not set. Skipping NewsAPI (optional source).")

        sec_filings = self._fetch_sec_filings(sources_used, sources_failed)

        all_headlines = _deduplicate(all_headlines)
        grouped = route_to_groups(all_headlines)

        logger.info(
            "Aggregation complete. %d unique headlines routed across %d groups. "
            "Sources used: %d, failed: %d.",
            len(all_headlines),
            sum(1 for v in grouped.values() if v),
            len(set(sources_used)),
            len(set(sources_failed)),
        )
        for key, items in grouped.items():
            logger.info("  %-16s %d candidate stories", key, len(items))

        return {
            "market_snapshot": market_snapshot,
            "macro_data": macro_data,
            "groups": grouped,
            "all_headlines": all_headlines[:120],
            "earnings_calendar": earnings_calendar,
            "economic_calendar": economic_calendar,
            "sec_filings": sec_filings,
            "sources_used": sorted(set(sources_used)),
            "sources_failed": sorted(set(sources_failed)),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def _fetch_market_snapshot(
        self, sources_used: list, sources_failed: list
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        any_success = False

        end_date = datetime.now()
        start_date = end_date - timedelta(days=21)
        d1 = start_date.strftime("%Y%m%d")
        d2 = end_date.strftime("%Y%m%d")

        for group_name, tickers in TICKER_GROUPS.items():
            snapshot[group_name] = {}

            for key, (stooq_sym, yahoo_sym, label, fmt) in tickers.items():
                # Yahoo is primary. Stooq began returning an HTML block page
                # instead of CSV for every symbol, so it is a fallback only.
                data = self._fetch_yahoo_quote(yahoo_sym, label, fmt)
                if data is None:
                    data = self._fetch_stooq_quote(stooq_sym, label, fmt, d1, d2)

                if data is not None:
                    snapshot[group_name][key] = data
                    any_success = True
                else:
                    snapshot[group_name][key] = {
                        "label": label, "format": fmt, "value": None,
                        "change_pct": None, "direction": "flat",
                    }
                    sources_failed.append(f"market:{group_name}:{key}")

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
                if r.get("Close", "N/A") not in ("N/A", "", None)
            ]
            if len(rows) < 2:
                return None
            prev_close = float(rows[-2]["Close"])
            current = float(rows[-1]["Close"])
            return _quote_dict(label, fmt, current, prev_close, rows[-1].get("Date", ""))
        except Exception as exc:
            logger.debug("Stooq failed for %s: %s", symbol, exc)
            return None

    def _fetch_yahoo_quote(self, symbol: str, label: str, fmt: str) -> dict | None:
        """
        Current price and the correct prior close from Yahoo's chart endpoint.

        The prior close has to be derived from the close series rather than
        taken from metadata. meta.previousClose is consistently null, and
        meta.chartPreviousClose is the close immediately BEFORE the requested
        range, so pairing it with regularMarketPrice yields a multi-week move
        masquerading as a daily change.

        So: regularMarketPrice is the current print. If the last bar in the
        series is that same print, the prior close is the bar before it;
        otherwise the last bar is itself the prior close and the live price is
        a newer session not yet in the array.
        """
        try:
            resp = self.session.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "1mo"},
                timeout=12,
            )
            resp.raise_for_status()
            result = resp.json()["chart"]["result"][0]
            meta = result.get("meta") or {}
            closes = [
                float(v) for v in result["indicators"]["quote"][0]["close"]
                if v is not None
            ]
            if not closes:
                return None

            live = meta.get("regularMarketPrice")
            if live is None:
                if len(closes) < 2:
                    return None
                current, prev_close = closes[-1], closes[-2]
            else:
                current = float(live)
                same_bar = abs(closes[-1] - current) <= max(abs(current) * 1e-6, 1e-9)
                if same_bar:
                    if len(closes) < 2:
                        return None
                    prev_close = closes[-2]
                else:
                    prev_close = closes[-1]

            as_of = ""
            ts = meta.get("regularMarketTime")
            if ts:
                as_of = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )

            return _quote_dict(label, fmt, current, prev_close, as_of)
        except Exception as exc:
            logger.debug("Yahoo failed for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # FRED
    # ------------------------------------------------------------------

    def _fetch_fred_data(
        self, sources_used: list, sources_failed: list
    ) -> dict[str, Any]:
        if not cfg.fred_api_key:
            logger.warning(
                "FRED_API_KEY not set. Rates, funding, credit spreads and macro "
                "data will all be missing, which removes the core of the briefing."
            )
            return {}
        if len(cfg.fred_api_key) != 32:
            logger.error(
                "FRED_API_KEY must be the 32-character key from "
                "fredaccount.stlouisfed.org (got length %d).",
                len(cfg.fred_api_key),
            )
            sources_failed.append("fred:invalid_api_key")
            return {}

        macro: dict[str, Any] = {}
        base = "https://api.stlouisfed.org/fred/series/observations"

        for name, spec in FRED_SERIES.items():
            series_id = spec["id"]
            try:
                resp = self.session.get(
                    base,
                    params={
                        "series_id": series_id,
                        "api_key": cfg.fred_api_key,
                        "limit": spec.get("obs", 30),
                        "sort_order": "desc",
                        "file_type": "json",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                obs = resp.json().get("observations", [])

                # Newest first from the API; keep only real numeric prints.
                series = [
                    (o["date"], float(o["value"]))
                    for o in obs
                    if o.get("value") not in (".", "", None)
                ]
                if not series:
                    sources_failed.append(f"fred:{series_id}:empty")
                    continue

                entry = _transform_series(series, spec)
                if entry is None:
                    sources_failed.append(f"fred:{series_id}:insufficient_history")
                    continue

                entry["series_id"] = series_id
                if spec.get("percentile"):
                    entry.update(_percentile_context([v for _, v in series]))
                macro[name] = entry

            except requests.HTTPError as exc:
                detail = ""
                if exc.response is not None:
                    try:
                        detail = exc.response.json().get("error_message", "")
                    except Exception:
                        detail = exc.response.text[:200]
                logger.warning("FRED failed for %s: %s %s", series_id, exc, detail)
                sources_failed.append(f"fred:{series_id}")
            except Exception as exc:
                logger.warning("FRED failed for %s: %s", series_id, exc)
                sources_failed.append(f"fred:{series_id}")

        if macro:
            sources_used.append("fred")
            logger.info("FRED: %d of %d series retrieved.", len(macro), len(FRED_SERIES))
        return macro

    # ------------------------------------------------------------------
    # Finnhub
    # ------------------------------------------------------------------

    def _fetch_finnhub_news(self, category: str, sources_failed: list) -> list[dict]:
        try:
            resp = self.session.get(
                "https://finnhub.io/api/v1/news",
                params={"category": category, "token": cfg.finnhub_api_key},
                timeout=15,
            )
            resp.raise_for_status()
            items = []
            for art in resp.json()[:60]:
                headline = _clean_text(art.get("headline", ""))
                if not headline:
                    continue
                published = datetime.fromtimestamp(
                    art.get("datetime", 0), tz=timezone.utc
                ).isoformat()
                if not _is_recent(published):
                    continue
                items.append({
                    "headline": headline,
                    "summary": _clean_text(art.get("summary", ""))[:700],
                    "url": art.get("url", ""),
                    "source": f"finnhub:{category}",
                    "source_name": art.get("source") or "Finnhub",
                    "published": published,
                    "fingerprint": _fingerprint(headline),
                })
            return items
        except Exception as exc:
            logger.warning("Finnhub news failed (%s): %s", category, exc)
            sources_failed.append(f"finnhub:{category}")
            return []

    def _fetch_finnhub_earnings(self, sources_used: list, sources_failed: list) -> list[dict]:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            week_out = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            resp = self.session.get(
                "https://finnhub.io/api/v1/calendar/earnings",
                params={"from": today, "to": week_out, "token": cfg.finnhub_api_key},
                timeout=15,
            )
            resp.raise_for_status()
            earnings = resp.json().get("earningsCalendar", [])[:40]
            sources_used.append("finnhub_earnings")
            return earnings
        except Exception as exc:
            logger.warning("Finnhub earnings calendar failed: %s", exc)
            sources_failed.append("finnhub_earnings")
            return []

    def _fetch_finnhub_economic_calendar(
        self, sources_used: list, sources_failed: list
    ) -> list[dict]:
        """
        Finnhub's economic calendar, which carries consensus estimates.

        This endpoint is premium-only. On the free tier it returns 403, which is
        expected rather than broken, so it is logged quietly and the FRED
        release-dates calendar covers the schedule instead (without consensus).
        """
        try:
            resp = self.session.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"token": cfg.finnhub_api_key},
                timeout=15,
            )
            if resp.status_code in (401, 403):
                logger.info(
                    "Finnhub economic calendar requires a paid plan. "
                    "Using the FRED release schedule instead."
                )
                return []
            resp.raise_for_status()
            events = resp.json().get("economicCalendar", [])
            # Keep US events in the next five days, highest impact first.
            today = datetime.now().date()
            horizon = today + timedelta(days=5)
            filtered = []
            for e in events:
                try:
                    when = datetime.strptime(e.get("time", "")[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                if today <= when <= horizon:
                    filtered.append(e)
            filtered.sort(key=lambda e: (e.get("time", ""), -_impact_rank(e)))
            sources_used.append("finnhub_economic_calendar")
            return filtered[:20]
        except Exception as exc:
            logger.warning("Finnhub economic calendar failed: %s", exc)
            sources_failed.append("finnhub_economic_calendar")
            return []

    # ------------------------------------------------------------------
    # FRED release schedule
    # ------------------------------------------------------------------

    def _fetch_fred_release_dates(
        self, sources_used: list, sources_failed: list
    ) -> list[dict]:
        """
        Upcoming economic releases from FRED's release-dates endpoint.

        This is the free substitute for a paid economic calendar. It gives the
        authoritative WHAT and WHEN for every federal statistical release, which
        is what "What to Watch" needs; it does not carry consensus estimates, so
        the newsletter says so rather than implying a number it does not have.

        FRED publishes hundreds of releases, most of them irrelevant, so results
        are filtered to the market-moving set below.
        """
        if not cfg.fred_api_key:
            return []

        today = datetime.now().date()
        horizon = today + timedelta(days=8)

        try:
            resp = self.session.get(
                "https://api.stlouisfed.org/fred/releases/dates",
                params={
                    "api_key": cfg.fred_api_key,
                    "file_type": "json",
                    "realtime_start": today.isoformat(),
                    "realtime_end": horizon.isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc",
                    "limit": 1000,
                },
                timeout=20,
            )
            resp.raise_for_status()
            entries = resp.json().get("release_dates", [])

            events: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for entry in entries:
                name = entry.get("release_name", "")
                impact = _release_impact(name)
                if not impact:
                    continue
                key = (name, entry.get("date", ""))
                if key in seen:
                    continue
                seen.add(key)
                events.append({
                    "time": entry.get("date", ""),
                    "country": "US",
                    "event": name,
                    "actual": None,
                    "estimate": None,
                    "prev": None,
                    "impact": impact,
                })

            if events:
                sources_used.append("fred_releases")
                logger.info(
                    "FRED release schedule: %d market-moving releases in the next 8 days.",
                    len(events),
                )
            return events[:20]
        except Exception as exc:
            logger.warning("FRED release schedule failed: %s", exc)
            sources_failed.append("fred_releases")
            return []

    # ------------------------------------------------------------------
    # NewsAPI
    # ------------------------------------------------------------------

    def _fetch_newsapi(self, query: str, group_key: str, sources_failed: list) -> list[dict]:
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
                timeout=15,
            )
            resp.raise_for_status()
            items = []
            for art in resp.json().get("articles", []):
                headline = _clean_text(art.get("title", ""))
                if not headline or "[Removed]" in headline:
                    continue
                items.append({
                    "headline": headline,
                    "summary": _clean_text(art.get("description", ""))[:700],
                    "url": art.get("url", ""),
                    "source": "newsapi",
                    "source_name": (art.get("source") or {}).get("name", "NewsAPI"),
                    "published": art.get("publishedAt", ""),
                    "fingerprint": _fingerprint(headline),
                    "group_hint": group_key,
                })
            return items
        except Exception as exc:
            logger.warning("NewsAPI failed for %s: %s", group_key, exc)
            sources_failed.append(f"newsapi:{group_key}")
            return []

    # ------------------------------------------------------------------
    # RSS
    # ------------------------------------------------------------------

    def _fetch_rss(self, source_key: str, url: str, sources_failed: list) -> list[dict]:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": _BROWSER_UA})
            if feed.bozo and not feed.entries:
                raise ValueError(f"feed parse error: {feed.bozo_exception}")

            publisher = SOURCE_NAMES.get(source_key.split("_")[0], source_key)
            items = []
            for entry in feed.entries[:30]:
                title = _clean_text(entry.get("title", ""))
                if not title:
                    continue
                published = _parse_date(entry.get("published", entry.get("updated", "")))
                if not _is_recent(published):
                    continue
                items.append({
                    "headline": title,
                    "summary": _clean_text(
                        entry.get("summary", entry.get("description", ""))
                    )[:700],
                    "url": entry.get("link", ""),
                    "source": source_key,
                    "source_name": publisher,
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

    def _fetch_sec_filings(self, sources_used: list, sources_failed: list) -> list[dict]:
        """
        Latest 8-K filings from EDGAR's official current-filings Atom feed.

        The browse-edgar getcurrent endpoint is the documented, stable way to
        read the live filing stream. SEC policy requires a descriptive
        User-Agent, set below.
        """
        url = (
            "https://www.sec.gov/cgi-bin/browse-edgar"
            "?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom"
        )
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": _SEC_USER_AGENT})
            if not feed.entries:
                raise ValueError("no entries returned")

            filings = []
            for entry in feed.entries[:25]:
                title = _clean_text(entry.get("title", ""))
                # Titles arrive as "8-K - COMPANY NAME (0001234567) (Filer)"
                entity = title
                match = re.search(r"-\s*(.+?)\s*\(\d{7,10}\)", title)
                if match:
                    entity = match.group(1).strip()
                filings.append({
                    "entity": entity,
                    "form_type": (title.split("-")[0].strip() or "8-K")[:12],
                    "file_date": _parse_date(
                        entry.get("updated", entry.get("published", ""))
                    )[:10],
                    "url": entry.get("link", ""),
                })
            sources_used.append("sec_edgar")
            return filings
        except Exception as exc:
            logger.warning("SEC EDGAR failed: %s", exc)
            sources_failed.append("sec_edgar")
            return []


# ---------------------------------------------------------------------------
# Quote construction
# ---------------------------------------------------------------------------

# A single-session move beyond these bounds is almost always a data artifact
# (a stale baseline, a split, a bad bar) rather than a real move. Publishing a
# wrong number is worse than publishing none, so the change is suppressed and
# the level is kept.
_MAX_PLAUSIBLE_MOVE_PCT: dict[str, float] = {
    "index": 12.0,
    "price": 20.0,
    "fx": 6.0,
    "yield": 25.0,
    "crypto": 40.0,
}


def _quote_dict(
    label: str, fmt: str, current: float, prev_close: float, as_of: str = ""
) -> dict:
    change_pct = ((current - prev_close) / prev_close * 100) if prev_close else 0.0

    limit = _MAX_PLAUSIBLE_MOVE_PCT.get(fmt, 25.0)
    if abs(change_pct) > limit:
        logger.warning(
            "%s: implausible %.1f%% session move (%.4f vs %.4f). "
            "Suppressing the change and keeping the level.",
            label, change_pct, current, prev_close,
        )
        return {
            "label": label, "format": fmt, "value": current,
            "prev_close": None, "change": None, "change_pct": None,
            "direction": "flat", "as_of": as_of, "suspect": True,
        }

    return {
        "label": label,
        "format": fmt,
        "value": current,
        "prev_close": prev_close,
        "change": round(current - prev_close, 4),
        "change_pct": round(change_pct, 2),
        "direction": (
            "up" if change_pct >= 0.05 else "down" if change_pct <= -0.05 else "flat"
        ),
        "as_of": as_of,
    }


# ---------------------------------------------------------------------------
# FRED transforms
# ---------------------------------------------------------------------------

def _transform_series(series: list[tuple[str, float]], spec: dict) -> dict | None:
    """
    series arrives newest-first as (date, value).

    Returns a uniform entry: value, prev_value, date, prev_date, plus the
    underlying level where the value is a computed rate.
    """
    transform = spec.get("transform", "level")

    if transform == "level":
        latest_date, latest = series[0]
        prev = series[1][1] if len(series) > 1 else None
        return {
            "value": latest,
            "prev_value": prev,
            "date": latest_date,
            "prev_date": series[1][0] if len(series) > 1 else "",
        }

    if transform == "diff":
        if len(series) < 3:
            return None
        latest_date, latest = series[0]
        prior = series[1][1]
        prior2 = series[2][1]
        return {
            "value": round(latest - prior, 1),
            "prev_value": round(prior - prior2, 1),
            "date": latest_date,
            "prev_date": series[1][0],
            "level": latest,
        }

    if transform == "yoy_from_index":
        # Monthly index: compare to the print twelve months earlier.
        if len(series) < 14:
            return None
        latest_date, latest = series[0]
        year_ago = series[12][1]
        prior = series[1][1]
        prior_year_ago = series[13][1]
        if not year_ago or not prior_year_ago:
            return None
        return {
            "value": round((latest / year_ago - 1) * 100, 2),
            "prev_value": round((prior / prior_year_ago - 1) * 100, 2),
            "date": latest_date,
            "prev_date": series[1][0],
            "level": latest,
        }

    return None


def _percentile_context(values: list[float]) -> dict:
    """1-year min, max and percentile rank of the latest observation."""
    window = values[:252] if len(values) > 252 else values
    if len(window) < 20:
        return {}
    latest = window[0]
    below = sum(1 for v in window if v < latest)
    return {
        "min_1y": round(min(window), 4),
        "max_1y": round(max(window), 4),
        "percentile_1y": round(below / len(window) * 100, 1),
    }


def _impact_rank(event: dict) -> int:
    impact = str(event.get("impact", "")).lower()
    return {"high": 3, "medium": 2, "low": 1}.get(impact, 0)


# FRED release names that actually move markets, by impact tier. Matched as
# case-insensitive substrings against the official release name.
_HIGH_IMPACT_RELEASES = (
    "consumer price index",
    "employment situation",
    "personal income and outlays",
    "gross domestic product",
    "producer price index",
    "advance monthly sales for retail",
    "job openings and labor turnover",
    "h.15 selected interest rates",
)
_MEDIUM_IMPACT_RELEASES = (
    "unemployment insurance weekly claims",
    "industrial production",
    "new residential construction",
    "consumer sentiment",
    "consumer confidence",
    "durable goods",
    "new residential sales",
    "existing home sales",
    "u.s. international trade in goods and services",
    "employment cost index",
    "productivity and costs",
    "senior loan officer opinion survey",
    "beige book",
    "household debt and credit",
)


def _release_impact(name: str) -> str | None:
    """high / medium for market-moving releases, None to filter the rest out."""
    lowered = (name or "").lower()
    if any(term in lowered for term in _HIGH_IMPACT_RELEASES):
        return "high"
    if any(term in lowered for term in _MEDIUM_IMPACT_RELEASES):
        return "medium"
    return None


# ---------------------------------------------------------------------------
# Group routing
# ---------------------------------------------------------------------------

def route_to_groups(headlines: list[dict]) -> dict[str, list[dict]]:
    """
    Assign each headline to the group whose desk would own it.

    Product-group keywords carry a weight premium so that, for example, a
    take-private of a software company routes to Sponsors or M&A rather than
    only to TMT. Each headline lands on its single best group, plus its second
    best when the scores are close, so a deal can appear on both the coverage
    and product desk that would staff it.
    """
    grouped: dict[str, list[dict]] = {k: [] for k in ROUTED_GROUPS}

    for item in headlines:
        text = (
            item.get("headline", "") + " " + item.get("summary", "")
        ).lower()

        scores: dict[str, float] = {}
        for key in ROUTED_GROUPS:
            meta = ALL_GROUPS[key]
            weight = (
                PRODUCT_KEYWORD_WEIGHT if meta.get("kind") == "product"
                else COVERAGE_KEYWORD_WEIGHT
            )
            hits = sum(1 for kw in meta.get("keywords", []) if kw in text)
            if hits:
                # Diminishing returns past the first few hits keeps a single
                # keyword-stuffed headline from dominating every group.
                scores[key] = (hits ** 0.7) * weight

        # An explicit NewsAPI query hint is worth about one keyword hit.
        hint = item.get("group_hint")
        if hint in scores:
            scores[hint] += 0.5
        elif hint in grouped:
            scores[hint] = 0.6

        if not scores:
            continue

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_key, best_score = ranked[0]
        if best_score < MIN_ROUTING_SCORE:
            continue

        enriched = dict(item)
        enriched["routing_score"] = round(best_score, 2)
        grouped[best_key].append(enriched)

        # Cross-post to a strong runner-up so deals reach both desks.
        if len(ranked) > 1:
            second_key, second_score = ranked[1]
            if second_score >= max(MIN_ROUTING_SCORE, best_score * 0.75):
                cross = dict(item)
                cross["routing_score"] = round(second_score, 2)
                cross["cross_posted"] = True
                grouped[second_key].append(cross)

    # Rank within each group: routing confidence first, then recency.
    for key in grouped:
        grouped[key] = sorted(
            grouped[key],
            key=lambda x: (x.get("routing_score", 0), x.get("published", "")),
            reverse=True,
        )[:18]

    return grouped


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _is_recent(published_iso: str, max_hours: int = 36) -> bool:
    try:
        pub = datetime.fromisoformat(published_iso)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - pub).total_seconds() < max_hours * 3600
    except Exception:
        return True  # unparseable dates are kept rather than silently dropped


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"&#\d+;", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(date_str: str) -> str:
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        import email.utils
        return email.utils.parsedate_to_datetime(date_str).isoformat()
    except Exception:
        return date_str


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", (text or "").lower())[:80]
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
