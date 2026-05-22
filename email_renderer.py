"""
Email rendering module.

Takes the structured market snapshot (from yfinance) and synthesized briefing
JSON (from Claude) and renders them into the final HTML email via Jinja2.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

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
        # Expose the _section macro inline — it's defined at the bottom of the
        # template file and called as a Jinja2 macro, so no extra setup needed.

    def render(
        self,
        market_snapshot: dict[str, Any],
        briefing: dict[str, Any],
    ) -> str:
        """
        Render the full HTML email.

        Args:
            market_snapshot: Real-time market data from news_aggregator.
            briefing: Structured JSON from ai_synthesizer.

        Returns:
            Complete HTML string ready for delivery.
        """
        template = self.env.get_template("briefing.html")

        now = datetime.now()

        context = _build_context(now, market_snapshot, briefing)

        try:
            html = template.render(**context)
        except Exception as exc:
            logger.error("Template rendering failed: %s", exc, exc_info=True)
            raise

        logger.info("Email rendered successfully (%d characters).", len(html))
        return html


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(
    now: datetime,
    market_snapshot: dict[str, Any],
    briefing: dict[str, Any],
) -> dict[str, Any]:
    day_of_week = now.strftime("%A")
    date_long = now.strftime("%B %d, %Y")
    generated_time = now.strftime("%I:%M %p")
    generated_at = now.strftime("%Y-%m-%d %H:%M ET")

    # Build preheader from top story
    top = briefing.get("top_story", {})
    if not isinstance(top, dict):
        top = {}

    preheader = top.get("headline", "Your morning financial intelligence briefing.")
    summary = top.get("summary")
    if summary:
        preheader = str(summary)[:120]

    # Sources cited
    sources_used = briefing.get("sources_used", [])
    if not isinstance(sources_used, list):
        sources_used = []
    if not sources_used:
        sources_used = ["Reuters", "CNBC", "MarketWatch", "Yahoo Finance", "SEC EDGAR"]

    def _section_list(key: str) -> list:
        val = briefing.get(key, [])
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            return [val]
        return []

    return {
        "date_long": date_long,
        "day_of_week": day_of_week,
        "generated_time": generated_time,
        "generated_at": generated_at,
        "preheader_text": preheader,
        "unsubscribe_url": _get_unsubscribe_url(),
        # Market data
        "market_snapshot": market_snapshot,
        # Synthesized sections (with safe fallbacks for all keys)
        "top_story": top,
        "markets_macro": _section_list("markets_macro"),
        "corporate_intelligence": _section_list("corporate_intelligence"),
        "tech_ai_watch": _section_list("tech_ai_watch"),
        "risk_radar": _section_list("risk_radar"),
        "data_points": _section_list("data_points"),
        "what_to_watch": _section_list("what_to_watch"),
        "sources_used": sources_used,
        "generation_notes": str(briefing.get("generation_notes", "")),
    }


def _get_unsubscribe_url() -> str:
    import os
    return os.environ.get("UNSUBSCRIBE_URL", "mailto:unsubscribe@example.com")
