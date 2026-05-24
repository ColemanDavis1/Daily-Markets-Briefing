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
    ) -> str:
        template = self.env.get_template("briefing.html")
        context = _build_context(datetime.now(ZoneInfo("America/New_York")), market_snapshot, briefing)
        html = template.render(**context)
        logger.info("Email rendered (%d characters).", len(html))
        return html


def _build_context(
    now: datetime,
    market_snapshot: dict[str, Any],
    briefing: dict[str, Any],
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

    # Sources
    sources: list[str] = []
    for src in ["stooq", "finnhub", "fred", "newsapi", "reuters", "cnbc"]:
        sources.append(src.upper())

    return {
        "date_long": date_long,
        "day_of_week": day_of_week,
        "generated_time": generated_time,
        "generated_at": generated_at,
        "preheader_text": preheader,
        "unsubscribe_url": os.environ.get("UNSUBSCRIBE_URL", "mailto:unsubscribe@example.com"),
        "market_snapshot": market_snapshot,
        "sections": sections,
        "sources_list": ", ".join(sources),
    }
