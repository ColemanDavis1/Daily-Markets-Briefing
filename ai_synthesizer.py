"""
AI synthesis module — Google Gemini backend.

Passes collected raw data to the Gemini API and receives structured JSON
for each section of the morning briefing. Falls back to rule-based compilation
when the API is unavailable (quota, errors, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import google.generativeai as genai
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config import get_config
from news_aggregator import CATEGORY_KEYWORDS

logger = logging.getLogger(__name__)
cfg = get_config()

_MODEL_FALLBACKS = (
    "gemini-2.5-flash",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
)

SYSTEM_PROMPT = """You are the chief markets editor of a premier financial intelligence publication.
Synthesize the provided headlines into a morning briefing for executives.

Rules:
- Tone: executive, direct, zero filler.
- Return ONLY valid JSON (no markdown).
- Required keys: top_story, markets_macro, corporate_intelligence, tech_ai_watch,
  risk_radar, data_points, what_to_watch, sources_used, generation_notes.
- top_story: {headline, summary, source, url}
- Section arrays: items with headline, body, source (risk_radar also needs risk_level: high|medium|low).
- what_to_watch items: headline, timing, context.
- data_points: metric, value, context.
- markets_macro: 4-6 items; corporate_intelligence: 4-6; tech_ai_watch: 3-4;
  risk_radar: 2-3; data_points: 3-5; what_to_watch: exactly 3.
- Use "[DATA UNAVAILABLE — check source directly]" only when inputs are truly empty."""

FALLBACK_SYSTEM_PROMPT = """Financial editor. Synthesize headlines into briefing JSON only.
Keys: top_story, markets_macro, corporate_intelligence, tech_ai_watch, risk_radar,
data_points, what_to_watch, sources_used, generation_notes. Be concise."""


def _is_quota_error(exc: BaseException) -> bool:
    err = str(exc).lower()
    return "429" in err or "quota" in err or "resource_exhausted" in err


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    err = str(exc).lower()
    return _is_quota_error(exc) or any(
        x in err for x in ("503", "overloaded", "unavailable", "deadline")
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class AISynthesizer:
    def __init__(self) -> None:
        genai.configure(api_key=cfg.google_api_key)

    def synthesize(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Convert raw aggregated data into structured briefing JSON."""
        user_content = _build_user_message(raw_data)

        try:
            return self._call_api(user_content, system_prompt=SYSTEM_PROMPT)
        except Exception as exc:
            logger.warning("Primary synthesis failed (%s). Retrying simplified.", exc)
            try:
                return self._call_api(user_content, system_prompt=FALLBACK_SYSTEM_PROMPT)
            except Exception as retry_exc:
                logger.error("Gemini unavailable (%s). Using headline fallback.", retry_exc)
                return _fallback_briefing_from_headlines(raw_data, str(retry_exc))

    def _call_api(self, user_content: str, *, system_prompt: str) -> dict[str, Any]:
        models_to_try: list[str] = []
        for name in (cfg.gemini_model, *_MODEL_FALLBACKS):
            if name and name not in models_to_try:
                models_to_try.append(name)

        last_exc: Exception | None = None
        for model_name in models_to_try:
            try:
                return self._generate_with_model(
                    model_name, user_content, system_prompt=system_prompt
                )
            except Exception as exc:
                last_exc = exc
                if _is_quota_error(exc):
                    logger.error("Gemini quota exceeded — skipping further models.")
                    raise
                err = str(exc).lower()
                retryable = (
                    "404" in err
                    or "not found" in err
                    or "no longer available" in err
                    or "json" in err
                    or "no text" in err
                    or "blocked" in err
                )
                if retryable:
                    logger.warning("Model %s failed (%s). Trying next.", model_name, exc)
                    continue
                raise

        raise last_exc or RuntimeError("No Gemini models available")

    @retry(
        retry=retry_if_exception(_is_retryable_gemini_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=20, max=60),
        reraise=True,
    )
    def _generate_with_model(
        self, model_name: str, user_content: str, *, system_prompt: str
    ) -> dict[str, Any]:
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )

        response = model.generate_content(user_content)
        raw_text = _extract_response_text(response)

        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        briefing = json.loads(raw_text)
        return _normalize_briefing(briefing)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_response_text(response: Any) -> str:
    try:
        text = response.text
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(f"Gemini returned no text: {exc}") from exc

    if not text or not str(text).strip():
        raise RuntimeError("Gemini returned empty response text")
    return str(text).strip()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _normalize_briefing(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _fallback_briefing_from_headlines(
            {"headlines": []}, f"Invalid briefing type: {type(raw).__name__}"
        )

    top = raw.get("top_story")
    if not isinstance(top, dict):
        top = {
            "headline": str(top) if top else "[DATA UNAVAILABLE]",
            "summary": "",
            "source": "N/A",
            "url": None,
        }

    return {
        "top_story": top,
        "markets_macro": _as_list(raw.get("markets_macro")),
        "corporate_intelligence": _as_list(raw.get("corporate_intelligence")),
        "tech_ai_watch": _as_list(raw.get("tech_ai_watch")),
        "risk_radar": _as_list(raw.get("risk_radar")),
        "data_points": _as_list(raw.get("data_points")),
        "what_to_watch": _as_list(raw.get("what_to_watch")),
        "sources_used": _as_list(raw.get("sources_used")),
        "generation_notes": str(raw.get("generation_notes", "")),
    }


def _build_user_message(raw_data: dict[str, Any]) -> str:
    parts: list[str] = []
    now = datetime.now().strftime("%A, %B %d, %Y")
    parts.append(f"DATE: {now}\n")

    snapshot = raw_data.get("market_snapshot", {})
    if snapshot:
        parts.append("=== MARKET SNAPSHOT ===")
        for key, data in snapshot.items():
            if data.get("value") is not None:
                val = data["value"]
                chg = data.get("change_pct")
                label = data.get("label", key)
                chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
                parts.append(f"  {label}: {_format_value(val, data.get('format', ''))} ({chg_str})")
        parts.append("")

    headlines = raw_data.get("headlines", [])
    if headlines:
        parts.append(f"=== HEADLINES ({len(headlines)} items) ===")
        for i, item in enumerate(headlines[:20], 1):
            src = item.get("source", "")
            headline = item.get("headline", "")
            summary = item.get("summary", "")
            parts.append(f"{i}. [{src}] {headline}")
            if summary:
                parts.append(f"   {summary[:200]}")
        parts.append("")

    parts.append("Return JSON briefing for today's date. Focus on market-moving items.")
    return "\n".join(parts)


def _format_value(value: float, fmt: str) -> str:
    if fmt == "yield":
        return f"{value:.2f}%"
    if fmt == "price":
        return f"${value:,.2f}"
    if fmt == "crypto":
        return f"${value:,.0f}"
    return f"{value:,.2f}"


def _categorize_headline(item: dict) -> str:
    text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
    scores = {
        cat: sum(1 for kw in keywords if kw in text)
        for cat, keywords in CATEGORY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "markets_macro"


def _item_from_headline(item: dict, *, risk: bool = False) -> dict[str, Any]:
    body = item.get("summary", "").strip() or "See source for full coverage."
    entry: dict[str, Any] = {
        "headline": item.get("headline", "Untitled"),
        "body": body[:400],
        "source": item.get("source", "N/A"),
    }
    if risk:
        entry["risk_level"] = "medium"
    return entry


def _fallback_briefing_from_headlines(
    raw_data: dict[str, Any], error_msg: str
) -> dict[str, Any]:
    """Build a readable briefing from RSS headlines when Gemini is unavailable."""
    headlines: list[dict] = list(raw_data.get("headlines") or [])
    buckets: dict[str, list[dict]] = {
        "markets_macro": [],
        "corporate_intelligence": [],
        "tech_ai_watch": [],
        "risk_radar": [],
    }

    for item in headlines:
        cat = _categorize_headline(item)
        if len(buckets[cat]) < 6:
            buckets[cat].append(_item_from_headline(item, risk=(cat == "risk_radar")))

    if not headlines:
        return _empty_briefing(error_msg)

    top_item = headlines[0]
    top_story = {
        "headline": top_item.get("headline", "Morning markets update"),
        "summary": (top_item.get("summary") or top_item.get("headline", ""))[:500],
        "source": top_item.get("source", "N/A"),
        "url": top_item.get("url"),
    }

    def _fill(key: str, n: int) -> list[dict]:
        items = buckets[key]
        if items:
            return items[:n]
        return [_item_from_headline(h) for h in headlines[:n]]

    sources_used = sorted({h.get("source", "") for h in headlines if h.get("source")})

    note = (
        "AI synthesis unavailable (Gemini API quota or error). "
        "Briefing compiled automatically from headlines. "
        f"Details: {error_msg[:180]}"
    )

    return {
        "top_story": top_story,
        "markets_macro": _fill("markets_macro", 5),
        "corporate_intelligence": _fill("corporate_intelligence", 5),
        "tech_ai_watch": _fill("tech_ai_watch", 4),
        "risk_radar": _fill("risk_radar", 3),
        "data_points": [
            {
                "metric": "Headlines collected",
                "value": str(len(headlines)),
                "context": "Live RSS aggregation succeeded; enable Gemini billing for AI summaries.",
            }
        ],
        "what_to_watch": [
            {
                "headline": h.get("headline", "Key development"),
                "timing": "Today",
                "context": (h.get("summary") or "")[:200] or "Monitor for updates.",
            }
            for h in headlines[1:4]
        ],
        "sources_used": sources_used,
        "generation_notes": note,
    }


def _empty_briefing(error_msg: str) -> dict[str, Any]:
    msg = f"No headlines available. {error_msg[:200]}"
    stub = {"headline": msg, "body": msg, "source": "N/A"}
    return {
        "top_story": {"headline": msg, "summary": msg, "source": "N/A", "url": None},
        "markets_macro": [stub],
        "corporate_intelligence": [],
        "tech_ai_watch": [],
        "risk_radar": [],
        "data_points": [],
        "what_to_watch": [],
        "sources_used": [],
        "generation_notes": msg,
    }
