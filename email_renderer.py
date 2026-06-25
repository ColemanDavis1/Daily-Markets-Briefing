"""Email rendering module — builds Jinja2 context and renders HTML."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import get_config

logger = logging.getLogger(__name__)
cfg = get_config()

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class EmailRenderer:
    def __init__(self) -> None:
        self.env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(
        self,
        market_snapshot: dict[str, Any],
        briefing: dict[str, Any],
        *,
        macro_data: dict[str, Any] | None = None,
        earnings_calendar: list[dict[str, Any]] | None = None,
        economic_calendar: list[dict[str, Any]] | None = None,
        sec_filings: list[dict[str, Any]] | None = None,
    ) -> str:
        template = self.env.get_template("briefing.html")
        context = _build_context(
            datetime.now(ZoneInfo("America/New_York")),
            market_snapshot,
            briefing,
            macro_data=macro_data or {},
            earnings_calendar=earnings_calendar or [],
            economic_calendar=economic_calendar or [],
            sec_filings=sec_filings or [],
        )
        html = template.render(**context)
        logger.info("Email rendered (%d characters).", len(html))
        return html


# FRED series display order and labels (matches news_aggregator.FRED_SERIES)
_FRED_DISPLAY: list[tuple[str, str]] = [
    ("fed_funds_rate", "Fed Funds Rate"),
    ("cpi_yoy", "CPI Index"),
    ("core_cpi", "Core CPI Index"),
    ("core_pce", "Core PCE Index"),
    ("unemployment", "Unemployment Rate"),
    ("real_gdp_growth", "Real GDP Growth"),
    ("yield_spread_10y2y", "10Y-2Y Spread"),
    ("mortgage_30y", "30Y Mortgage Rate"),
    ("ppi", "PPI Index"),
    ("industrial_prod", "Industrial Production"),
    ("retail_sales", "Retail Sales"),
    ("housing_starts", "Housing Starts"),
]


def _build_context(
    now: datetime,
    market_snapshot: dict[str, Any],
    briefing: dict[str, Any],
    *,
    macro_data: dict[str, Any],
    earnings_calendar: list[dict[str, Any]],
    economic_calendar: list[dict[str, Any]],
    sec_filings: list[dict[str, Any]],
) -> dict[str, Any]:
    day_of_week = now.strftime("%A")
    date_long = now.strftime("%B %d, %Y")
    generated_time = now.strftime("%I:%M %p")
    generated_at = now.strftime("%Y-%m-%d %H:%M ET")

    # Build sections list in defined order
    from ai_synthesizer import SECTION_CONFIGS
    sections = []
    for key in SECTION_CONFIGS:
        if key in briefing:
            sections.append(briefing[key])

    # Preheader from first section's narrative
    preheader = "Your morning financial intelligence briefing."
    if sections and sections[0].get("narrative"):
        preheader = sections[0]["narrative"][:120].replace("\n", " ")

    # Sector ETFs sorted by performance (best to worst) for ranked display
    sectors_raw = market_snapshot.get("sectors", {})
    sorted_sectors = sorted(
        [(k, v) for k, v in sectors_raw.items() if v.get("change_pct") is not None],
        key=lambda x: x[1].get("change_pct", 0),
        reverse=True,
    )

    # Sources
    sources: list[str] = []
    for src in ["stooq", "finnhub", "fred", "newsapi", "reuters", "cnbc", "sec edgar"]:
        sources.append(src.upper())

    macro_items = []
    for key, label in _FRED_DISPLAY:
        data = macro_data.get(key, {})
        if data.get("value") is not None:
            macro_items.append({"label": label, **data})

    return {
        "date_long": date_long,
        "day_of_week": day_of_week,
        "generated_time": generated_time,
        "generated_at": generated_at,
        "preheader_text": preheader,
        "unsubscribe_url": os.environ.get("UNSUBSCRIBE_URL", "mailto:unsubscribe@example.com"),
        "market_snapshot": market_snapshot,
        "sorted_sectors": sorted_sectors,
        "macro_items": macro_items,
        "earnings_calendar": earnings_calendar[:20],
        "economic_calendar": economic_calendar[:15],
        "sec_filings": sec_filings[:10],
        "sections": sections,
        "sources_list": ", ".join(sources),
    }
