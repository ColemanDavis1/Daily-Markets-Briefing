"""
AI synthesis module — multi-call Gemini backend.

Makes one Gemini API call per section. Each call receives only the
headlines relevant to that section, producing a focused narrative +
structured data bullets rather than a compressed single-call summary.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

import google.generativeai as genai
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import get_config

logger = logging.getLogger(__name__)
cfg = get_config()

# ---------------------------------------------------------------------------
# Section definitions (order controls email layout)
# ---------------------------------------------------------------------------

SECTION_CONFIGS: dict[str, dict] = {
    "markets_macro": {
        "title": "Markets & Macro",
        "color": "#0A4A7A",
        "editorial_focus": (
            "Cover the dominant market theme, key index moves and their drivers, "
            "bond market dynamics, currency moves, and any significant technical levels. "
            "Explain the WHY behind moves — do not simply list what happened."
        ),
    },
    "corporate_earnings": {
        "title": "Corporate & Earnings",
        "color": "#1A3A5C",
        "editorial_focus": (
            "Lead with the most market-moving earnings report or corporate action. "
            "Cover: earnings beats/misses with guidance implications, M&A deals and "
            "strategic rationale, notable analyst calls with price target changes, "
            "significant executive changes. Connect each item to sector implications."
        ),
    },
    "technology_ai": {
        "title": "Technology & AI",
        "color": "#0A3A6A",
        "editorial_focus": (
            "Cover AI model releases and competitive dynamics, semiconductor supply "
            "and demand signals, major funding rounds and their market signals, "
            "regulatory developments in tech, and hardware/infrastructure buildout. "
            "Connect technology developments to investment and competitive implications."
        ),
    },
    "healthcare": {
        "title": "Healthcare",
        "color": "#0A5A4A",
        "editorial_focus": (
            "Cover FDA approvals/rejections and their revenue implications, clinical "
            "trial results with statistical context, pharma/biotech M&A and pipeline "
            "deals, insurance and reimbursement policy changes, hospital sector trends. "
            "Flag anything with near-term stock price implications."
        ),
    },
    "industrials": {
        "title": "Industrials",
        "color": "#3A2A6A",
        "editorial_focus": (
            "Cover manufacturing data releases (PMI, factory orders, capacity "
            "utilization), aerospace and defense contract awards, infrastructure "
            "spending developments, logistics and supply chain conditions, "
            "union/labor developments at major industrial companies."
        ),
    },
    "energy_commodities": {
        "title": "Energy & Commodities",
        "color": "#5A3A0A",
        "editorial_focus": (
            "Cover crude oil price action and OPEC+ production decisions, natural "
            "gas and LNG market dynamics, renewable energy policy and investment, "
            "metals markets (gold, copper, lithium as leading indicators), and "
            "agricultural commodities if relevant to inflation narrative."
        ),
    },
    "geopolitical_risk": {
        "title": "Geopolitical Risk",
        "color": "#5A0A0A",
        "editorial_focus": (
            "Frame every item in terms of direct market, supply chain, or regulatory "
            "impact. Cover: active conflicts with commodity/trade implications, "
            "tariff and sanctions developments, election outcomes and policy risk, "
            "emerging market stress. Do not cover geopolitics without a market angle."
        ),
    },
    "economic_data": {
        "title": "Economic Data & Fed",
        "color": "#0A3A2A",
        "editorial_focus": (
            "Lead with any data released today (CPI, PCE, jobs, GDP) and its "
            "implications for Fed policy. Cover Fed communications, rate expectations, "
            "yield curve dynamics. Use FRED data if provided to ground the narrative "
            "in actual figures. Distinguish consensus expectations from actual prints."
        ),
    },
    "what_to_watch": {
        "title": "What to Watch",
        "color": "#2A0A5A",
        "editorial_focus": (
            "List the 4-5 most important catalysts to monitor in the next 24-48 hours. "
            "Include: scheduled economic releases with consensus estimates, earnings "
            "reports with key metrics to watch, Fed speakers and their known stances, "
            "geopolitical developments with binary outcomes. Each item must explain "
            "WHY it matters and what the bull/bear scenario looks like."
        ),
    },
}

# ---------------------------------------------------------------------------
# Per-section system prompt template
# ---------------------------------------------------------------------------

SECTION_SYSTEM_PROMPT = """You are a senior editor at a premier financial intelligence publication.
You are writing the {section_title} section of today's morning briefing.

EDITORIAL FOCUS:
{editorial_focus}

OUTPUT FORMAT — return ONLY valid JSON, no markdown fences, matching this schema exactly:
{{
  "narrative": "2-3 paragraphs of editorial prose. Separate paragraphs with \\n\\n. Write in active voice, executive tone. Each paragraph 4-6 sentences. Synthesize themes — do not list headlines.",
  "bullets": [
    {{"label": "Company/Metric/Event", "value": "key number or fact", "note": "one sentence: implication or context"}}
  ]
}}

RULES:
- narrative: minimum 2 paragraphs, minimum 4 sentences each. This is a 15-minute read — be thorough.
- bullets: 4-6 items. Lead with the most market-relevant data points, specific numbers, ticker symbols.
- Flag analyst opinions with "(analyst view)" inline.
- If a headline has no market implication, exclude it.
- Never fabricate data. If inputs are thin, note it in generation_notes but still write what you can.
- CRITICAL: Only reference events explicitly present in today's provided headlines. Do NOT use your training knowledge to add events, prices, or company news not in the inputs. If a story is not in today's headlines, it did not happen today.
- Return ONLY the JSON object. Nothing else."""

# ---------------------------------------------------------------------------
# Verification prompt — second pass fact-check
# ---------------------------------------------------------------------------

VERIFY_SYSTEM_PROMPT = """You are a financial fact-checker reviewing a morning briefing section.
A colleague drafted the content below from the source headlines provided. Your job is to verify accuracy.

RULES:
1. Every specific claim (price, percentage, earnings figure, company action, data point) must be
   directly traceable to the source headlines or market data provided.
2. If a claim cannot be verified from the sources: soften it with "reportedly" or remove it entirely.
3. If a bullet "value" contains a specific number not present in the source data, clear it to "".
4. Never add new information. Only correct or remove what is unverifiable.
5. Analyst opinions must remain labeled "(analyst view)".
6. Preserve narrative length — do not summarise or shorten, only fix unsupported facts.
7. Return ONLY valid JSON matching this schema exactly:
{{
  "narrative": "...",
  "bullets": [{{"label": "...", "value": "...", "note": "..."}}]
}}"""

# ---------------------------------------------------------------------------
# Gemini model fallback chain
# ---------------------------------------------------------------------------

_MODEL_FALLBACKS = ("gemini-2.5-flash", "gemini-2.0-flash-001", "gemini-1.5-flash")


def _is_retryable(exc: BaseException) -> bool:
    err = str(exc).lower()
    return any(x in err for x in ("503", "overloaded", "unavailable", "deadline", "429", "quota"))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class AISynthesizer:
    def __init__(self) -> None:
        genai.configure(api_key=cfg.google_api_key)

    def synthesize(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """
        Run one Gemini call per section. Returns a dict keyed by section name,
        each containing {title, color, narrative, bullets}.
        """
        result: dict[str, Any] = {}
        first_section = True

        for section_key, section_cfg in SECTION_CONFIGS.items():
            if not first_section and cfg.gemini_section_delay_sec > 0:
                time.sleep(cfg.gemini_section_delay_sec)
            first_section = False

            logger.info("Synthesizing section: %s", section_cfg["title"])
            try:
                section_data = self._build_section_input(
                    section_key, section_cfg, raw_data
                )
                output = self._call_section(section_key, section_cfg, section_data)
                if _should_verify(section_key):
                    if cfg.gemini_section_delay_sec > 0:
                        time.sleep(cfg.gemini_section_delay_sec)
                    verified = self._verify_section(
                        section_key, section_cfg, section_data, output
                    )
                    narrative = verified.get("narrative", output.get("narrative", ""))
                    bullets = verified.get("bullets", output.get("bullets", []))
                else:
                    narrative = output.get("narrative", "")
                    bullets = output.get("bullets", [])

                result[section_key] = {
                    "title": section_cfg["title"],
                    "color": section_cfg["color"],
                    "narrative": narrative,
                    "bullets": bullets,
                }
            except Exception as exc:
                logger.error("Section %s failed: %s", section_key, exc)
                result[section_key] = _fallback_section(
                    section_key, section_cfg, raw_data, str(exc)
                )

        return result

    # ------------------------------------------------------------------
    # Input builder
    # ------------------------------------------------------------------

    def _build_section_input(
        self,
        section_key: str,
        section_cfg: dict,
        raw_data: dict[str, Any],
    ) -> str:
        parts: list[str] = []
        now = datetime.now().strftime("%A, %B %d, %Y")
        parts.append(f"DATE: {now}\nSECTION: {section_cfg['title']}\n")

        # Market snapshot for markets/macro section
        if section_key == "markets_macro":
            snap = raw_data.get("market_snapshot", {})
            if snap:
                parts.append("MARKET PRICES:")
                for key, data in snap.items():
                    if data.get("value") is not None:
                        chg = data.get("change_pct")
                        chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
                        parts.append(
                            f"  {data['label']}: {_fmt_value(data['value'], data.get('format',''))} "
                            f"({chg_str}) [{data.get('direction','')}]"
                        )
                parts.append("")

        # FRED macro data for economic/markets sections
        if section_key in ("economic_data", "markets_macro"):
            macro = raw_data.get("macro_data", {})
            if macro:
                parts.append("FRED MACRO DATA (Federal Reserve):")
                labels = {
                    "fed_funds_rate": "Fed Funds Rate",
                    "cpi_yoy": "CPI (latest reading)",
                    "core_cpi": "Core CPI",
                    "unemployment": "Unemployment Rate",
                    "gdp_growth": "GDP",
                    "yield_spread_10y2y": "10Y-2Y Yield Spread",
                    "mortgage_30y": "30Y Mortgage Rate",
                }
                for key, data in macro.items():
                    if data.get("value") is not None:
                        label = labels.get(key, key)
                        parts.append(
                            f"  {label}: {data['value']} (as of {data.get('date','')})"
                        )
                parts.append("")

        # Earnings calendar for corporate section
        if section_key in ("corporate_earnings", "what_to_watch"):
            earnings = raw_data.get("earnings_calendar", [])
            if earnings:
                parts.append(f"UPCOMING EARNINGS ({len(earnings)} companies this week):")
                for e in earnings[:15]:
                    sym = e.get("symbol", "")
                    date = e.get("date", "")
                    eps = e.get("epsEstimate")
                    eps_str = f" | EPS est: ${eps:.2f}" if eps else ""
                    parts.append(f"  {sym} — {date}{eps_str}")
                parts.append("")

        # Economic calendar for forward-looking sections
        if section_key in ("economic_data", "what_to_watch"):
            econ_cal = raw_data.get("economic_calendar", [])
            if econ_cal:
                parts.append(f"ECONOMIC CALENDAR ({len(econ_cal)} events):")
                for e in econ_cal[:10]:
                    event = e.get("event", "")
                    impact = e.get("impact", "")
                    actual = e.get("actual", "")
                    estimate = e.get("estimate", "")
                    parts.append(
                        f"  {event} | Impact: {impact} | "
                        f"Actual: {actual or 'pending'} | Est: {estimate or 'N/A'}"
                    )
                parts.append("")

        # SEC filings for corporate section
        if section_key == "corporate_earnings":
            filings = raw_data.get("sec_filings", [])
            if filings:
                parts.append(f"SEC 8-K FILINGS TODAY ({len(filings)}):")
                for f in filings[:10]:
                    parts.append(f"  {f.get('entity','')} — {f.get('form_type','')}")
                parts.append("")

        # Section-specific headlines
        sections = raw_data.get("sections", {})
        headlines = sections.get(section_key, [])

        # what_to_watch gets headlines from all sections
        if section_key == "what_to_watch":
            all_headlines = []
            for s_headlines in sections.values():
                all_headlines.extend(s_headlines[:5])
            headlines = all_headlines[:30]

        if headlines:
            parts.append(f"HEADLINES ({len(headlines)} items):")
            for i, item in enumerate(headlines[:25], 1):
                src = item.get("source", "")
                headline = item.get("headline", "")
                summary = item.get("summary", "")
                parts.append(f"{i}. [{src}] {headline}")
                if summary:
                    parts.append(f"   {summary[:250]}")
            parts.append("")

        if not headlines and section_key != "what_to_watch":
            parts.append(
                "NOTE: No specific headlines available for this section today. "
                "Write based on general market context and note limited data availability."
            )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------

    def _call_section(
        self, section_key: str, section_cfg: dict, user_content: str
    ) -> dict[str, Any]:
        system_prompt = SECTION_SYSTEM_PROMPT.format(
            section_title=section_cfg["title"],
            editorial_focus=section_cfg["editorial_focus"],
        )

        models_to_try = []
        for m in (cfg.gemini_model, *_MODEL_FALLBACKS):
            if m and m not in models_to_try:
                models_to_try.append(m)

        last_exc: Exception | None = None
        for model_name in models_to_try:
            try:
                return self._generate(model_name, system_prompt, user_content)
            except Exception as exc:
                last_exc = exc
                err = str(exc).lower()
                if any(x in err for x in ("404", "not found", "no longer available")):
                    logger.warning("Model %s not available, trying next.", model_name)
                    continue
                raise

        raise last_exc or RuntimeError("No Gemini models available")

    def _verify_section(
        self,
        section_key: str,
        section_cfg: dict,
        source_input: str,
        generated: dict[str, Any],
    ) -> dict[str, Any]:
        """Fact-check generated content against source headlines. Falls back to original on error."""
        try:
            user_content = (
                "SOURCE DATA (ground truth):\n"
                + source_input
                + "\n\nGENERATED CONTENT TO VERIFY:\n"
                + json.dumps(generated, ensure_ascii=False)
            )
            models_to_try = []
            for m in (cfg.gemini_model, *_MODEL_FALLBACKS):
                if m and m not in models_to_try:
                    models_to_try.append(m)

            for model_name in models_to_try:
                try:
                    model = genai.GenerativeModel(
                        model_name=model_name,
                        system_instruction=VERIFY_SYSTEM_PROMPT,
                        generation_config=genai.GenerationConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                            max_output_tokens=4096,
                        ),
                    )
                    response = model.generate_content(user_content)
                    raw = response.text.strip()
                    if raw.startswith("```"):
                        raw = re.sub(r"^```[a-z]*\n?", "", raw)
                        raw = re.sub(r"\n?```$", "", raw)
                    verified = json.loads(raw)
                    bullets = verified.get("bullets", [])
                    if isinstance(bullets, dict):
                        bullets = [bullets]
                    verified["bullets"] = bullets
                    logger.info("  Verified section: %s", section_cfg["title"])
                    return verified
                except Exception as exc:
                    err = str(exc).lower()
                    if any(x in err for x in ("404", "not found", "no longer available")):
                        continue
                    raise
        except Exception as exc:
            logger.warning("Verification failed for %s, using original: %s", section_key, exc)
        return generated

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=15, max=60),
        reraise=True,
    )
    def _generate(
        self, model_name: str, system_prompt: str, user_content: str
    ) -> dict[str, Any]:
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )

        response = model.generate_content(user_content)

        try:
            raw_text = response.text.strip()
        except (ValueError, AttributeError) as exc:
            raise RuntimeError(f"Gemini returned no text: {exc}") from exc

        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        result = json.loads(raw_text)

        # Normalise bullets
        bullets = result.get("bullets", [])
        if isinstance(bullets, dict):
            bullets = [bullets]
        result["bullets"] = bullets

        logger.info(
            "  %s — %s tokens in / %s tokens out",
            model_name,
            getattr(response.usage_metadata, "prompt_token_count", "?"),
            getattr(response.usage_metadata, "candidates_token_count", "?"),
        )

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _should_verify(section_key: str) -> bool:
    if not cfg.verify_sections:
        return False
    if cfg.verify_only_sections:
        return section_key in cfg.verify_only_sections
    return True


def _fallback_section(
    section_key: str,
    section_cfg: dict,
    raw_data: dict[str, Any],
    error_msg: str,
) -> dict[str, Any]:
    """Compile a section from RSS headlines when Gemini is unavailable."""
    sections = raw_data.get("sections", {})
    headlines = list(sections.get(section_key, []))

    if section_key == "what_to_watch":
        headlines = []
        for items in sections.values():
            headlines.extend(items[:3])
        headlines = headlines[:12]

    bullets = [
        {
            "label": item.get("headline", "Headline"),
            "value": item.get("source", ""),
            "note": (item.get("summary") or "")[:220],
        }
        for item in headlines[:6]
    ]

    if headlines:
        lead = headlines[0]
        narrative = (
            f"{lead.get('headline', '')}. "
            f"{(lead.get('summary') or '').strip()}"
        ).strip()
        if len(headlines) > 1:
            narrative += (
                f"\n\nAdditional developments: "
                + "; ".join(h.get("headline", "") for h in headlines[1:4])
            )
    else:
        narrative = (
            "No section-specific headlines were available today. "
            f"(Gemini unavailable: {error_msg[:120]})"
        )

    return {
        "title": section_cfg["title"],
        "color": section_cfg["color"],
        "narrative": narrative[:1200],
        "bullets": bullets,
    }


def _fmt_value(value: float, fmt: str) -> str:
    if fmt == "yield":
        return f"{value:.2f}%"
    elif fmt == "price":
        return f"${value:,.2f}"
    elif fmt == "crypto":
        return f"${value:,.0f}"
    elif fmt == "fx":
        return f"{value:.4f}"
    return f"{value:,.2f}"
