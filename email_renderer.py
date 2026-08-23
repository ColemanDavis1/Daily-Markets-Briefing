"""
Email rendering.

Builds the Jinja2 context and renders the newsletter. Also derives the subject
line and preheader from the day's lead, so the inbox preview says something
specific rather than "Morning Briefing".
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ai_synthesizer import build_section_plan
from config import get_config
from groups import COVERAGE_GROUPS, PRODUCT_GROUPS

logger = logging.getLogger(__name__)
cfg = get_config()

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_ET = ZoneInfo("America/New_York")


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
        *,
        briefing: dict[str, Any],
        engine: dict[str, Any],
        raw_data: dict[str, Any],
    ) -> tuple[str, str]:
        """Returns (html, subject)."""
        now = datetime.now(_ET)
        context = _build_context(now, briefing, engine, raw_data)
        html = self.env.get_template("briefing.html").render(**context)
        logger.info(
            "Rendered %d characters, %d sections, %d key numbers.",
            len(html), len(context["sections"]), len(context["key_numbers"]),
        )
        return html, context["subject"]


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def _build_context(
    now: datetime,
    briefing: dict[str, Any],
    engine: dict[str, Any],
    raw_data: dict[str, Any],
) -> dict[str, Any]:
    meta = briefing.get("_meta", {}) or {}
    plan = build_section_plan()

    # Assemble sections in plan order, skipping any that never got built.
    sections: list[dict] = []
    for entry in plan:
        section = briefing.get(entry["key"])
        if not section:
            continue
        section = dict(section)
        section["anchor"] = f"sec-{entry['key'].replace('_', '-')}"
        sections.append(section)

    lead = next((s for s in sections if s["key"] == "editor_note"), None)
    body_sections = [s for s in sections if s["key"] != "editor_note"]

    # Navigation grid, split into the three visual bands of the issue.
    nav_bands = [
        ("The Desk", [s for s in body_sections if s["kind"] == "lead"]),
        ("Coverage Groups", [s for s in body_sections if s["key"] in COVERAGE_GROUPS]),
        ("Product Groups", [s for s in body_sections if s["key"] in PRODUCT_GROUPS]),
        ("Forward Look", [s for s in body_sections if s["kind"] == "standing"]),
    ]
    nav_bands = [(label, items) for label, items in nav_bands if items]

    # Band headers inserted into the body flow at the right positions.
    first_coverage = next((s["key"] for s in body_sections if s["key"] in COVERAGE_GROUPS), None)
    first_product = next((s["key"] for s in body_sections if s["key"] in PRODUCT_GROUPS), None)
    first_standing = next((s["key"] for s in body_sections if s["kind"] == "standing"), None)
    band_headers = {
        first_coverage: {
            "label": "Coverage Groups",
            "blurb": "One story per industry desk, chosen for what it teaches about the sector.",
        },
        first_product: {
            "label": "Product Groups",
            "blurb": "One story per product desk: how capital actually moved today, and at what price.",
        },
        first_standing: {
            "label": "Forward Look",
            "blurb": "",
        },
    }
    band_headers.pop(None, None)

    subject, preheader = _subject_and_preheader(now, lead, engine)

    quiet_desks = [s["title"] for s in body_sections if s.get("quiet_day")]
    unwritten = [s["title"] for s in body_sections if not s.get("available", True)]

    return {
        # Masthead
        "date_long": now.strftime("%B %-d, %Y") if _supports_dash() else now.strftime("%B %d, %Y"),
        "day_of_week": now.strftime("%A"),
        "generated_at": now.strftime("%I:%M %p ET").lstrip("0"),
        "edition_label": now.strftime("%Y-%m-%d"),
        "subject": subject,
        "preheader_text": preheader,

        # Content
        "lead": lead,
        "sections": body_sections,
        "nav_bands": nav_bands,
        "band_headers": band_headers,
        "key_numbers": engine.get("key_numbers", []),

        # Data tables
        "equity": engine.get("equity", {}),
        "curve": engine.get("curve", {}),
        "funding": engine.get("funding", {}),
        "inflation": engine.get("inflation", {}),
        "credit": engine.get("credit", {}),
        "policy": engine.get("policy", {}),
        "cross_asset": engine.get("cross_asset", {}),
        "econ_rows": engine.get("econ", {}).get("rows", []),

        # Calendars
        "economic_calendar": _clean_econ_calendar(raw_data.get("economic_calendar") or []),
        "earnings_calendar": _clean_earnings(raw_data.get("earnings_calendar") or []),
        "sec_filings": (raw_data.get("sec_filings") or [])[:10],

        # Footer
        "sources_used": ", ".join(_pretty_sources(raw_data.get("sources_used") or [])),
        "sources_failed_count": len(raw_data.get("sources_failed") or []),
        "edition_kind": meta.get("edition", "newsletter"),
        "degraded_sections": meta.get("degraded_sections", []),
        "verified_count": meta.get("verified_count", 0),
        "quiet_desks": quiet_desks,
        "unwritten": unwritten,
        "unsubscribe_url": cfg.unsubscribe_url,
    }


def _supports_dash() -> bool:
    """%-d is POSIX only; Windows uses %#d. Detect once rather than guessing."""
    try:
        datetime.now().strftime("%-d")
        return True
    except ValueError:
        return False


def _subject_and_preheader(now: datetime, lead: dict | None, engine: dict) -> tuple[str, str]:
    date_bit = now.strftime("%b %d")

    headline = ""
    one_thing = ""
    if lead:
        headline = (lead.get("headline") or "").strip()
        one_thing = (lead.get("one_thing") or "").strip()

    if headline:
        # Keep subjects inbox-legible.
        trimmed = headline if len(headline) <= 68 else headline[:65].rsplit(" ", 1)[0] + "..."
        subject = f"Morning Desk, {date_bit}: {trimmed}"
    else:
        subject = f"Morning Desk, {date_bit}"

    preheader = one_thing or headline
    if not preheader:
        policy = engine.get("policy", {})
        curve = engine.get("curve", {})
        bits = []
        if curve.get("spread_2s10s_bp") is not None:
            bits.append(f"2s10s {curve['spread_2s10s_bp']}bp")
        if policy.get("target_range"):
            bits.append(f"Fed at {policy['target_range']}")
        preheader = ". ".join(bits) if bits else "Markets, rates and the desks that matter."

    return subject, preheader[:160]


def _clean_econ_calendar(events: list[dict]) -> list[dict]:
    out = []
    for e in events[:12]:
        out.append({
            "time": str(e.get("time", ""))[:16],
            "country": e.get("country", ""),
            "event": e.get("event", ""),
            "actual": _num(e.get("actual")),
            "estimate": _num(e.get("estimate")),
            "prev": _num(e.get("prev")),
            "impact": str(e.get("impact", "")).title(),
        })
    return out


def _clean_earnings(events: list[dict]) -> list[dict]:
    out = []
    for e in events[:14]:
        symbol = e.get("symbol")
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "date": e.get("date", ""),
            "eps_estimate": _num(e.get("epsEstimate")),
            "revenue_estimate": _billions(e.get("revenueEstimate")),
            "hour": {"bmo": "Pre-open", "amc": "Post-close"}.get(e.get("hour"), ""),
        })
    return out


def _num(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        f = float(value)
        return f"{f:,.2f}".rstrip("0").rstrip(".") if abs(f) < 1000 else f"{f:,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _billions(value: Any) -> str:
    try:
        return f"${float(value) / 1e9:,.2f}B"
    except (TypeError, ValueError):
        return ""


_SOURCE_LABELS = {
    "market_data": "Stooq / Yahoo",
    "fred": "FRED",
    "finnhub": "Finnhub",
    "finnhub_earnings": "Finnhub",
    "finnhub_economic_calendar": "Finnhub",
    "newsapi": "NewsAPI",
    "sec_edgar": "SEC EDGAR",
}


def _pretty_sources(sources: list[str]) -> list[str]:
    seen: list[str] = []
    for s in sources:
        label = _SOURCE_LABELS.get(s)
        if not label:
            prefix = s.split("_")[0]
            label = {
                "reuters": "Reuters", "cnbc": "CNBC", "wsj": "WSJ",
                "marketwatch": "MarketWatch", "ft": "FT",
                "fed": "Federal Reserve", "treasury": "US Treasury",
            }.get(prefix, prefix.title())
        if label not in seen:
            seen.append(label)
    return seen
