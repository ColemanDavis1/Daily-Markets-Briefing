"""
AI synthesis module — Google Gemini backend.

Passes collected raw data to the Gemini API and receives structured JSON
for each section of the morning briefing. Uses response_mime_type="application/json"
to enforce clean JSON output natively.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import google.generativeai as genai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from config import get_config

logger = logging.getLogger(__name__)
cfg = get_config()

# ---------------------------------------------------------------------------
# Output JSON schema (injected into the system prompt)
# ---------------------------------------------------------------------------

OUTPUT_SCHEMA = {
    "top_story": {
        "headline": "string",
        "summary": "3-4 sentences. Executive tone. Lead with impact.",
        "source": "string",
        "url": "string or null",
    },
    "markets_macro": [
        {
            "headline": "string",
            "body": "2-3 sentences. Lead with key fact, follow with market implication.",
            "source": "string",
        }
    ],
    "corporate_intelligence": [
        {"headline": "string", "body": "2-3 sentences.", "source": "string"}
    ],
    "tech_ai_watch": [
        {"headline": "string", "body": "2-3 sentences.", "source": "string"}
    ],
    "risk_radar": [
        {
            "headline": "string",
            "body": "2-3 sentences.",
            "risk_level": "high | medium | low",
            "source": "string",
        }
    ],
    "data_points": [
        {
            "metric": "string",
            "value": "string",
            "context": "1 sentence explaining significance.",
        }
    ],
    "what_to_watch": [
        {
            "headline": "string",
            "timing": "string e.g. 'Today 2:00 PM ET'",
            "context": "1-2 sentences on why it matters.",
        }
    ],
    "sources_used": ["array of source names cited"],
    "generation_notes": "string — note any data gaps or caveats",
}

SYSTEM_PROMPT = f"""You are the chief markets editor of a premier financial intelligence publication.
Your task: synthesize raw news headlines and summaries into a structured morning briefing for
C-suite executives, institutional investors, and senior risk officers.

EDITORIAL STANDARDS:
- Tone: executive, direct, zero filler. Every sentence must carry information.
- Lead each bullet with the key fact, then the market or business implication.
- Distinguish reported facts from analyst opinions — flag opinions with "(analyst view)" inline.
- If data is unavailable for a section, output the placeholder string:
  "[DATA UNAVAILABLE — check source directly]" rather than fabricating content.
- Prioritize items with direct market-moving potential.
- top_story summary: max 4 sentences. All body fields: max 3 sentences.

OUTPUT: Return ONLY valid JSON matching this exact schema:

{json.dumps(OUTPUT_SCHEMA, indent=2)}

COUNT TARGETS:
- markets_macro: 4-6 items
- corporate_intelligence: 4-6 items
- tech_ai_watch: 3-4 items
- risk_radar: 2-3 items
- data_points: 3-5 items
- what_to_watch: exactly 3 items"""

FALLBACK_SYSTEM_PROMPT = """You are a financial editor. Synthesize the provided headlines into a morning
briefing. Return ONLY valid JSON with these top-level keys: top_story, markets_macro,
corporate_intelligence, tech_ai_watch, risk_radar, data_points, what_to_watch,
sources_used, generation_notes. Keep all text concise. Use placeholder strings for missing data."""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class AISynthesizer:
    def __init__(self) -> None:
        genai.configure(api_key=cfg.google_api_key)

    def synthesize(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """
        Convert raw aggregated data into structured briefing JSON.
        Retries once with a simplified prompt on failure.
        """
        user_content = _build_user_message(raw_data)

        try:
            return self._call_api(user_content, system_prompt=SYSTEM_PROMPT)
        except Exception as exc:
            logger.warning("Primary synthesis failed (%s). Retrying with simplified prompt.", exc)
            try:
                return self._call_api(user_content, system_prompt=FALLBACK_SYSTEM_PROMPT)
            except Exception as retry_exc:
                logger.error("Synthesis retry also failed: %s", retry_exc)
                return _placeholder_briefing(str(retry_exc))

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(10),
        retry=retry_if_exception_type(Exception),
    )
    def _call_api(self, user_content: str, *, system_prompt: str) -> dict[str, Any]:
        model = genai.GenerativeModel(
            model_name=cfg.gemini_model,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=4096,
            ),
        )

        response = model.generate_content(user_content)
        raw_text = response.text.strip()

        # Strip accidental markdown fences just in case
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        briefing = json.loads(raw_text)

        logger.info(
            "Synthesis complete via %s. Input tokens: %s, Output tokens: %s",
            cfg.gemini_model,
            getattr(response.usage_metadata, "prompt_token_count", "N/A"),
            getattr(response.usage_metadata, "candidates_token_count", "N/A"),
        )

        return briefing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_user_message(raw_data: dict[str, Any]) -> str:
    parts: list[str] = []
    now = datetime.now().strftime("%A, %B %d, %Y")
    parts.append(f"DATE: {now}\n")

    snapshot = raw_data.get("market_snapshot", {})
    if snapshot:
        parts.append("=== MARKET SNAPSHOT (pre-market/overnight) ===")
        for key, data in snapshot.items():
            if data.get("value") is not None:
                val = data["value"]
                chg = data.get("change_pct")
                label = data.get("label", key)
                direction = data.get("direction", "")
                chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
                parts.append(
                    f"  {label}: {_format_value(val, data.get('format', ''))} "
                    f"({chg_str}) [{direction}]"
                )
        parts.append("")

    headlines = raw_data.get("headlines", [])
    if headlines:
        parts.append(f"=== RAW HEADLINES ({len(headlines)} items, sorted by relevance) ===")
        for i, item in enumerate(headlines[:60], 1):
            src = item.get("source", "")
            headline = item.get("headline", "")
            summary = item.get("summary", "")
            parts.append(f"{i}. [{src}] {headline}")
            if summary:
                parts.append(f"   Summary: {summary[:300]}")
        parts.append("")

    filings = raw_data.get("sec_filings", [])
    if filings:
        parts.append(f"=== SEC EDGAR FILINGS (today, {len(filings)} items) ===")
        for f in filings[:10]:
            parts.append(
                f"  {f.get('entity', '')} — {f.get('form_type', '')} "
                f"filed {f.get('file_date', '')}"
            )
        parts.append("")

    parts.append(
        "Synthesize the above into the required JSON briefing. "
        f"Today's date: {now}. "
        "Focus on items with the highest market-moving or strategic significance."
    )

    return "\n".join(parts)


def _format_value(value: float, fmt: str) -> str:
    if fmt == "yield":
        return f"{value:.2f}%"
    elif fmt == "price":
        return f"${value:,.2f}"
    elif fmt == "crypto":
        return f"${value:,.0f}"
    return f"{value:,.2f}"


def _placeholder_briefing(error_msg: str) -> dict[str, Any]:
    placeholder = "[DATA UNAVAILABLE — synthesis pipeline failed. Check logs.]"
    return {
        "top_story": {
            "headline": placeholder,
            "summary": placeholder,
            "source": "N/A",
            "url": None,
        },
        "markets_macro": [{"headline": placeholder, "body": placeholder, "source": "N/A"}],
        "corporate_intelligence": [{"headline": placeholder, "body": placeholder, "source": "N/A"}],
        "tech_ai_watch": [{"headline": placeholder, "body": placeholder, "source": "N/A"}],
        "risk_radar": [
            {"headline": placeholder, "body": placeholder, "risk_level": "medium", "source": "N/A"}
        ],
        "data_points": [{"metric": "Pipeline Error", "value": "N/A", "context": error_msg[:200]}],
        "what_to_watch": [
            {"headline": placeholder, "timing": "N/A", "context": placeholder}
        ],
        "sources_used": [],
        "generation_notes": f"Synthesis failed: {error_msg}",
    }
