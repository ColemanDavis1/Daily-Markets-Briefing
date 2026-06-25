"""
AI synthesis module — multi-call Claude (Anthropic) backend.

Makes one Claude API call per section. Each call receives only the
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

import anthropic
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
            "Lead with the single most important market development and WHY it matters at a structural level.\n\n"
            "REQUIRED ANALYSIS — cover all that are supported by today's data:\n"
            "1. EQUITY STRUCTURE: Analyze S&P 500, NASDAQ, Dow, and Russell 2000 divergences. "
            "What do large-cap vs. small-cap and growth vs. value differentials reveal about risk appetite and breadth? Use specific index levels.\n"
            "2. SECTOR ROTATION: Identify the leading and lagging S&P sectors from today's ETF performance data. "
            "What does this rotation signal — risk-on/off, cyclical/defensive shift, growth/value regime change? "
            "Connect each sector move to its macro driver.\n"
            "3. YIELD CURVE: Analyze the full curve (2Y, 5Y, 10Y, 30Y). Is the curve inverted, normalizing, or steepening? "
            "What does the 2Y-10Y spread imply for recession probability and Fed expectations? "
            "How did rate moves affect equity valuations today?\n"
            "4. DOLLAR & CURRENCIES: Interpret DXY, EUR/USD, GBP/USD, and USD/JPY moves. "
            "What are the implications for US multinational earnings, dollar-denominated commodities, and EM stress?\n"
            "5. COMMODITY SIGNALS: What are gold (safe-haven/real-rate proxy), copper (industrial growth proxy), "
            "and oil saying? Are they confirming or contradicting the equity narrative? Flag any divergence.\n"
            "6. INTERNATIONAL CONTEXT: How do European and Asian indices compare to US markets today? "
            "What macro forces explain the divergences?\n"
            "7. CROSS-ASSET SYNTHESIS: Synthesize bonds, currencies, commodities, and equities into a unified thesis. "
            "Are the signals corroborating or divergent? What does the cross-asset picture imply for the week ahead?\n"
            "8. VOLATILITY: Interpret VIX level and trend relative to historical context."
        ),
    },
    "corporate_earnings": {
        "title": "Corporate & Earnings",
        "color": "#1A3A5C",
        "editorial_focus": (
            "Lead with the most market-moving earnings result or corporate action, fully quantified.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. EARNINGS RESULTS: For any reported quarter, state: actual EPS vs. consensus, actual revenue vs. consensus, "
            "the beat/miss percentage, and — most critically — what GUIDANCE implies vs. prior expectations.\n"
            "2. GUIDANCE ANALYSIS: Guidance is more important than reported results. "
            "What did management say about forward revenue, margins, and macro conditions? "
            "Did they raise, lower, or maintain outlook? What is the delta?\n"
            "3. M&A & CAPITAL ALLOCATION: For any deals, state deal value, premium to market, strategic rationale, "
            "and whether analysts view it as accretive or dilutive. Cover buyback authorizations and dividend changes.\n"
            "4. ANALYST CALLS: Include specific price target changes (old vs. new), the key thesis, "
            "and the sector implications of upgrades/downgrades.\n"
            "5. EXECUTIVE CHANGES: Frame any leadership changes in terms of strategic direction shift.\n"
            "6. UPCOMING CATALYSTS: Flag major companies reporting this week, what the consensus estimates are, "
            "and which specific metrics will determine the market reaction."
        ),
    },
    "technology_ai": {
        "title": "Technology & AI",
        "color": "#0A3A6A",
        "editorial_focus": (
            "Cover the most material technology and AI developments for institutional investors.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. AI COMPETITIVE DYNAMICS: New model releases, capability benchmarks, infrastructure investments. "
            "Who wins and loses competitively? Frame in terms of market share and valuation implications for major players.\n"
            "2. SEMICONDUCTOR SUPPLY CHAIN: Availability, pricing, lead times, and capacity expansion. "
            "Connect directly to major beneficiaries (NVDA, AMD, ASML, TSMC, AMAT).\n"
            "3. FUNDING & M&A: State valuations, investor composition, and what each deal signals about "
            "where institutional capital is flowing and which AI/tech themes are gaining traction.\n"
            "4. REGULATORY RISK: Antitrust actions, data privacy enforcement, AI governance developments. "
            "Frame specific market and operational impacts.\n"
            "5. INFRASTRUCTURE BUILDOUT: Data center, power, and networking capex commitments. "
            "Identify the picks-and-shovels beneficiaries and the capex cycle timeline.\n"
            "6. HYPERSCALER DYNAMICS: Azure, AWS, Google Cloud market share shifts and their implications "
            "for enterprise software and AI services demand."
        ),
    },
    "healthcare": {
        "title": "Healthcare",
        "color": "#0A5A4A",
        "editorial_focus": (
            "Cover FDA decisions, clinical data, and policy with institutional-grade analytical depth.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. FDA DECISIONS: For approvals — state indication, addressable patient population, "
            "projected peak sales (if analyst estimates available), competitive landscape, and pricing dynamics. "
            "For rejections/CRLs — state the deficiency and remediation pathway with timeline.\n"
            "2. CLINICAL DATA: Report primary endpoint results, statistical significance (p-values where available), "
            "comparison to prior clinical benchmarks, and what this means for the asset's commercial trajectory.\n"
            "3. M&A & LICENSING: State deal value, premium to market, pipeline asset acquired, "
            "strategic fit, and any projected synergies.\n"
            "4. POLICY & REIMBURSEMENT: CMS coverage decisions, IRA drug price negotiation developments, "
            "and their specific revenue impact by company.\n"
            "5. SECTOR POSITIONING: Is healthcare acting defensively (risk-off rotation) or "
            "offensively (drug cycle/innovation tailwind)?\n"
            "6. UPCOMING CATALYSTS: PDUFA dates, pivotal trial readouts, and policy decisions in the next 30-60 days."
        ),
    },
    "industrials": {
        "title": "Industrials",
        "color": "#3A2A6A",
        "editorial_focus": (
            "Cover manufacturing, defense, and logistics with quantified macro linkages.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. MANUFACTURING DATA: PMI readings (actual vs. consensus, sub-components: new orders, employment, "
            "prices paid, inventories). What is the manufacturing cycle signal — expansion, contraction, or inflection?\n"
            "2. DEFENSE CONTRACTING: Contract awards with dollar values, duration, strategic context, "
            "and which prime contractors benefit. Frame in budget and geopolitical context.\n"
            "3. INFRASTRUCTURE: Federal spending deployment, project awards, and materials demand implications "
            "from infrastructure legislation.\n"
            "4. LOGISTICS & SUPPLY CHAIN: Freight rates, inventory levels, port conditions — "
            "leading indicators of industrial demand and inflationary pressure.\n"
            "5. LABOR DYNAMICS: Union contract outcomes, wage settlements, and their direct margin implications "
            "for major industrial companies. "
            "6. CAPEX SIGNALS: Major equipment orders or plant investment as forward demand indicators "
            "for the industrial cycle."
        ),
    },
    "energy_commodities": {
        "title": "Energy & Commodities",
        "color": "#5A3A0A",
        "editorial_focus": (
            "Cover energy markets and commodities as both investment themes and macro indicators.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. OIL MARKET STRUCTURE: WTI and Brent absolute levels and daily move with specific drivers. "
            "OPEC+ production signals and compliance. US inventory data vs. expectations. "
            "What is the marginal cost context for current prices?\n"
            "2. ENERGY SECTOR: Upstream vs. downstream performance divergence. "
            "E&P vs. services vs. integrated companies. Refining margin dynamics.\n"
            "3. NATURAL GAS & LNG: Price dynamics, storage vs. 5-year average, LNG export flows and "
            "European/Asian demand signals.\n"
            "4. METALS AS MACRO SIGNALS: Copper as industrial growth proxy, gold as real-rate/safe-haven indicator, "
            "silver as industrial/monetary hybrid. Interpret current levels in that analytical framework — "
            "what are these metals signaling about growth and inflation expectations?\n"
            "5. ENERGY TRANSITION: Renewable capacity additions, battery/storage economics, policy developments. "
            "Frame implications for traditional energy investment thesis.\n"
            "6. COMMODITY INFLATION PASS-THROUGH: Connect price moves to CPI/PPI implications "
            "and corporate input cost/margin risk."
        ),
    },
    "geopolitical_risk": {
        "title": "Geopolitical Risk",
        "color": "#5A0A0A",
        "editorial_focus": (
            "Every geopolitical development must be translated into a specific market, supply chain, or regulatory impact. "
            "Do not cover geopolitics without a concrete market angle.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. TARIFF & TRADE: New tariffs, exemptions, or negotiations. State affected trade volume in dollars, "
            "sectors impacted, company-level winners and losers, and consumer price implications.\n"
            "2. SANCTIONS: New sanctions or enforcement actions. State affected commodities, financial flows, "
            "alternative sourcing implications, and which EM economies face collateral risk.\n"
            "3. ACTIVE CONFLICTS: Only cover if there are direct commodity, logistics, or defense spending implications. "
            "State the specific market impact with quantification where possible.\n"
            "4. ELECTION & POLICY RISK: Upcoming elections or regime changes that could materially affect "
            "market structure. Frame as binary scenario analysis with market outcomes.\n"
            "5. EMERGING MARKET STRESS: Sovereign debt, currency crises, or capital outflows with "
            "potential contagion to developed market assets.\n"
            "6. REGULATORY ENFORCEMENT: FTC, DOJ, EU competition authority actions that reshape "
            "corporate strategies and sector valuations."
        ),
    },
    "economic_data": {
        "title": "Economic Data & Fed",
        "color": "#0A3A2A",
        "editorial_focus": (
            "Lead with any data released today and its Fed policy implications. This section sets rate expectations.\n\n"
            "REQUIRED ANALYSIS:\n"
            "1. TODAY'S DATA RELEASES: For each release, state: actual vs. consensus, prior reading, "
            "any revision to prior month, and the directional delta from expectations. "
            "What does each print imply for the Fed? Be precise.\n"
            "2. INFLATION REGIME: Current CPI, Core CPI, and Core PCE (the Fed's preferred measure) "
            "in the context of the 2% target. Assess the trajectory — MoM, YoY, and 3-month annualized. "
            "Are we converging toward target or stalling?\n"
            "3. LABOR MARKET: Payrolls, unemployment rate, wage growth, and participation rate. "
            "Distinguish cyclical from structural weakness. Is the labor market cooling enough for the Fed?\n"
            "4. FED COMMUNICATIONS: Any FOMC member speeches or statements. "
            "Identify the hawkish vs. dovish divide and where consensus is shifting.\n"
            "5. RATE EXPECTATIONS: What does the market currently price for the next FOMC meeting "
            "and year-end? How did today's data move those probabilities?\n"
            "6. YIELD CURVE IMPLICATIONS: Connect today's economic data to the yield curve shape and "
            "what it implies for recession probability and the real economy.\n"
            "7. GROWTH TRAJECTORY: GDP, industrial production, retail sales, and housing — "
            "what is the composite picture of economic momentum?"
        ),
    },
    "what_to_watch": {
        "title": "What to Watch",
        "color": "#2A0A5A",
        "editorial_focus": (
            "Identify the 5-6 most important catalysts in the next 24-72 hours. "
            "This is the forward intelligence section — actionable, specific, time-bound.\n\n"
            "For EACH catalyst provide:\n"
            "- WHAT: Exact event or data release\n"
            "- WHEN: Specific time if known\n"
            "- WHY IT MATTERS: What market narrative it confirms or refutes\n"
            "- BULL SCENARIO: What would be market-positive and estimated market impact\n"
            "- BEAR SCENARIO: What would be market-negative and estimated market impact\n"
            "- CONSENSUS: Specific number or expectation if available\n\n"
            "REQUIRED CATALYSTS TO IDENTIFY:\n"
            "1. Scheduled economic data releases (CPI, PCE, payrolls, GDP, PMIs) with consensus estimates\n"
            "2. Fed speakers or FOMC events and their known policy stances\n"
            "3. Major earnings reports with EPS/revenue consensus and the one metric that will drive the reaction\n"
            "4. Geopolitical deadlines or binary events\n"
            "5. Technical levels: key S&P 500 support/resistance that could trigger systematic flows\n"
            "6. International market catalysts (ECB, BOJ, China policy)"
        ),
    },
}

# ---------------------------------------------------------------------------
# Per-section system prompt template
# ---------------------------------------------------------------------------

SECTION_SYSTEM_PROMPT = """You are the chief markets editor at the world's most authoritative financial intelligence publication — serving portfolio managers, hedge fund analysts, chief investment officers, and senior executives who make multi-billion-dollar decisions based on your analysis. Your writing combines the analytical rigor of a top-tier sell-side research note with the narrative clarity of award-winning financial journalism.

You are writing the {section_title} section of today's morning briefing.

YOUR JOB IS TO EXPLAIN, NOT TO REPEAT. The reader can already see the headlines and the raw numbers in a data table above your section. Your value is interpretation: tell them what the numbers and headlines actually MEAN, WHY they moved, and WHY it matters. Never restate a headline verbatim — translate it into insight. Write for a smart, engaged reader who is not a markets specialist: keep the institutional depth, but make every conclusion legible by explaining the mechanism behind it and briefly defining any jargon the first time you use it (e.g., "the 2s10s spread — the gap between 2- and 10-year Treasury yields, a classic recession gauge").

EDITORIAL MANDATE:
{editorial_focus}

OUTPUT FORMAT — return ONLY valid JSON, no markdown fences, matching this schema exactly:
{{
  "bottom_line": "1-2 plain-language sentences capturing the single most important takeaway of this section — what a busy reader must understand if they read nothing else. State the 'so what,' not just the 'what.'",
  "narrative": "4-5 paragraphs of expert financial analysis. Separate paragraphs with \\n\\n. Write with authority, precision, and quantitative rigor — but make every point legible by explaining the cause-and-effect mechanism. Minimum 4 sentences per paragraph. Synthesize multiple data points into a coherent analytical thesis — never list headlines sequentially. Every claim requires a specific number. For each key development follow the chain: what happened -> WHY it happened -> why it matters / what to watch next. Identify the bull and bear interpretation of key developments where relevant.",
  "bullets": [
    {{"label": "Ticker / Metric / Event", "value": "specific figure with units, sign, and context", "note": "a full explanatory sentence: WHY this moved or what it implies forward — not a restatement of the label"}}
  ]
}}

NON-NEGOTIABLE RULES:
1. Minimum 4 paragraphs, 4-5 sentences each. This is a deep, explanatory institutional read — no summaries, no padding, no headline lists.
2. Explain the mechanism behind every move. Not "stocks rose" and not even "S&P 500 gained 1.2% to 5,280" alone, but "the S&P 500 gained 1.2% to 5,280 as falling Treasury yields (down 8bps) lowered the discount rate on future earnings, which disproportionately lifts richly-valued technology names."
3. Define jargon briefly on first use so a non-specialist can follow the reasoning.
4. 5-7 bullet points. Lead with the most market-critical data. Include specific numbers in every bullet, and make each note explain significance.
5. Every consensus beat/miss must cite both the actual figure and the consensus estimate, then explain why the gap matters.
6. Flag analyst opinions with "(analyst view)." Attribute institutional views if sourced.
7. Frame key developments with asymmetry: what is the bull case? What is the bear case?
8. If a data point has no market implication, exclude it.
9. CRITICAL: Only reference events explicitly present in today's provided data. Do NOT use training knowledge to add prices, events, or company news not in the inputs.
10. Return ONLY the JSON object. Nothing else."""

# ---------------------------------------------------------------------------
# Light per-section system prompt — concise, curated
# ---------------------------------------------------------------------------

LIGHT_SECTION_SYSTEM_PROMPT = """You are the editor of a concise daily markets intelligence briefing. Your reader follows finance, the broader market, and sector/industry developments closely, but wants only the highest-signal takeaways today — not exhaustive coverage.

You are writing the {section_title} section.

YOUR JOB: curate hard. Surface ONLY the 2-4 most relevant and important developments in this area today, chosen for their materiality to markets and investors. Lead with what matters most. Explain the "so what" in plain language, briefly define any jargon, and tie each development to a market implication. If little of importance happened in this area today, say so in one sentence rather than padding.

Areas to weigh (cover only those with genuine, material news in today's data):
{editorial_focus}

OUTPUT FORMAT — return ONLY valid JSON, no markdown fences, matching this schema exactly:
{{
  "bottom_line": "1 sentence: the single most important takeaway of this section — the 'so what,' not just the 'what.'",
  "narrative": "1-2 tight paragraphs (3-4 sentences each), covering only the 2-4 most relevant developments. Every claim needs a specific number. Be high-signal and brief. If nothing material happened, say so in one sentence.",
  "bullets": [
    {{"label": "Ticker / Metric / Event", "value": "specific figure with units and sign", "note": "one short clause on why it matters"}}
  ]
}}

NON-NEGOTIABLE RULES:
1. Be concise and high-signal. No padding, no filler, no exhaustive headline lists.
2. 3-4 bullets maximum, most market-critical first. Include a specific number in each.
3. Every consensus beat/miss must cite the actual figure and the estimate.
4. Flag analyst opinions with "(analyst view)."
5. Only reference events explicitly present in today's provided data. Do NOT use training knowledge to add prices, events, or company news not in the inputs.
6. Return ONLY the JSON object. Nothing else."""

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
6. Preserve narrative length and the bottom_line — do not summarise or shorten, only fix unsupported facts.
7. Return ONLY valid JSON matching this schema exactly:
{{
  "bottom_line": "...",
  "narrative": "...",
  "bullets": [{{"label": "...", "value": "...", "note": "..."}}]
}}"""

# ---------------------------------------------------------------------------
# Robust JSON parsing
# ---------------------------------------------------------------------------

def _parse_json_object(text: str) -> dict[str, Any]:
    """
    Extract and parse the first complete JSON object from an LLM response.

    Tolerates two common LLM quirks:
      - trailing prose after the closing brace ("Extra data" errors)
      - literal newlines/tabs inside string values (strict-mode failures)
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    depth = 0
    in_str = False
    escape = False
    end = len(text)
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    candidate = text[start:end]
    # strict=False permits raw control characters (newlines, tabs) in strings.
    return json.loads(candidate, strict=False)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

def _is_retryable(exc: BaseException) -> bool:
    """Retry only on transient conditions — never on auth/bad-request/credit errors."""
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APITimeoutError,
                        anthropic.APIConnectionError, anthropic.InternalServerError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in (500, 502, 503, 504, 529)
    return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class AISynthesizer:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def synthesize(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """
        Run one Claude call per section. Returns a dict keyed by section name,
        each containing {title, color, narrative, bullets}.
        """
        result: dict[str, Any] = {}
        first_section = True

        for section_key, section_cfg in SECTION_CONFIGS.items():
            if not first_section and cfg.section_delay_sec > 0:
                time.sleep(cfg.section_delay_sec)
            first_section = False

            logger.info("Synthesizing section: %s", section_cfg["title"])
            try:
                section_data = self._build_section_input(
                    section_key, section_cfg, raw_data
                )
                output = self._call_section(section_key, section_cfg, section_data)
                if _should_verify(section_key):
                    if cfg.section_delay_sec > 0:
                        time.sleep(cfg.section_delay_sec)
                    verified = self._verify_section(
                        section_key, section_cfg, section_data, output
                    )
                    narrative = verified.get("narrative", output.get("narrative", ""))
                    bullets = verified.get("bullets", output.get("bullets", []))
                    bottom_line = verified.get(
                        "bottom_line", output.get("bottom_line", "")
                    )
                else:
                    narrative = output.get("narrative", "")
                    bullets = output.get("bullets", [])
                    bottom_line = output.get("bottom_line", "")

                result[section_key] = {
                    "title": section_cfg["title"],
                    "color": section_cfg["color"],
                    "bottom_line": bottom_line,
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

        snap = raw_data.get("market_snapshot", {})

        # Full cross-asset market data for markets/macro and what_to_watch
        if section_key in ("markets_macro", "what_to_watch"):
            _append_primary_indices(parts, snap)
            _append_sector_performance(parts, snap)
            _append_yield_curve(parts, snap)
            _append_fx(parts, snap)
            _append_commodities(parts, snap)
            _append_international(parts, snap)
            _append_crypto(parts, snap)
        elif section_key == "economic_data":
            _append_primary_indices(parts, snap)
            _append_yield_curve(parts, snap)
        else:
            # Brief market context for all other sections
            _append_primary_indices(parts, snap)

        # FRED macro data for economic and market sections
        if section_key in ("economic_data", "markets_macro", "what_to_watch"):
            macro = raw_data.get("macro_data", {})
            if macro:
                parts.append("FRED MACRO DATA (Federal Reserve — latest available readings):")
                fred_labels = {
                    "fed_funds_rate":     "Fed Funds Rate (%)",
                    "cpi_yoy":            "CPI YoY (%)",
                    "core_cpi":           "Core CPI YoY (%)",
                    "core_pce":           "Core PCE YoY (%) — Fed's preferred measure",
                    "unemployment":       "Unemployment Rate (%)",
                    "gdp_growth":         "GDP (billions $)",
                    "real_gdp_growth":    "Real GDP Growth QoQ Annualized (%)",
                    "yield_spread_10y2y": "10Y-2Y Yield Spread (pp, FRED)",
                    "mortgage_30y":       "30Y Mortgage Rate (%)",
                    "ppi":                "PPI All Commodities",
                    "industrial_prod":    "Industrial Production Index",
                    "retail_sales":       "Retail Sales ex Food Svcs",
                    "housing_starts":     "Housing Starts (thousands, ann.)",
                }
                for key, data in macro.items():
                    if data.get("value") is not None:
                        label = fred_labels.get(key, key)
                        prev = data.get("prev_value")
                        prev_str = f" | Prior: {prev}" if prev is not None else ""
                        parts.append(
                            f"  {label}: {data['value']} (as of {data.get('date','')}){prev_str}"
                        )
                parts.append("")

        # Earnings calendar for corporate and forward-looking sections
        if section_key in ("corporate_earnings", "what_to_watch"):
            earnings = raw_data.get("earnings_calendar", [])
            if earnings:
                parts.append(f"UPCOMING EARNINGS ({len(earnings)} companies this week):")
                for e in earnings[:20]:
                    sym = e.get("symbol", "")
                    date = e.get("date", "")
                    eps = e.get("epsEstimate")
                    rev = e.get("revenueEstimate")
                    eps_str = f" | EPS est: ${eps:.2f}" if eps else ""
                    rev_str = f" | Rev est: ${rev/1e9:.1f}B" if rev and rev > 1e8 else ""
                    parts.append(f"  {sym} — {date}{eps_str}{rev_str}")
                parts.append("")

        # Economic calendar
        if section_key in ("economic_data", "what_to_watch"):
            econ_cal = raw_data.get("economic_calendar", [])
            if econ_cal:
                parts.append(f"ECONOMIC CALENDAR ({len(econ_cal)} events):")
                for e in econ_cal[:15]:
                    event = e.get("event", "")
                    impact = e.get("impact", "")
                    actual = e.get("actual", "")
                    estimate = e.get("estimate", "")
                    prior = e.get("prev", "")
                    parts.append(
                        f"  {event} | Impact: {impact} | "
                        f"Actual: {actual or 'pending'} | Est: {estimate or 'N/A'} | Prior: {prior or 'N/A'}"
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

        if section_key == "what_to_watch":
            all_headlines = []
            for s_headlines in sections.values():
                all_headlines.extend(s_headlines[:5])
            headlines = all_headlines[:35]

        if headlines:
            parts.append(f"HEADLINES ({len(headlines)} items):")
            for i, item in enumerate(headlines[:25], 1):
                src = item.get("source", "")
                headline = item.get("headline", "")
                summary = item.get("summary", "")
                parts.append(f"{i}. [{src}] {headline}")
                if summary:
                    parts.append(f"   {summary[:300]}")
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
        template = (
            LIGHT_SECTION_SYSTEM_PROMPT
            if cfg.briefing_mode == "light"
            else SECTION_SYSTEM_PROMPT
        )
        system_prompt = template.format(
            section_title=section_cfg["title"],
            editorial_focus=section_cfg["editorial_focus"],
        )
        return self._generate(system_prompt, user_content, temperature=0.4)

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
            verified = self._generate(
                VERIFY_SYSTEM_PROMPT, user_content, temperature=0.1
            )
            logger.info("  Verified section: %s", section_cfg["title"])
            return verified
        except Exception as exc:
            logger.warning("Verification failed for %s, using original: %s", section_key, exc)
        return generated

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        reraise=True,
    )
    def _generate(
        self, system_prompt: str, user_content: str, temperature: float = 0.4
    ) -> dict[str, Any]:
        # Prefilling the assistant turn with "{" forces JSON-only output.
        response = self.client.messages.create(
            model=cfg.claude_model,
            max_tokens=cfg.claude_max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": "{"},
            ],
        )

        raw_text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not raw_text:
            raise RuntimeError("Claude returned no text")

        # Re-attach the prefilled opening brace, then parse defensively.
        if not raw_text.startswith("{"):
            raw_text = "{" + raw_text

        result = _parse_json_object(raw_text)

        bullets = result.get("bullets", [])
        if isinstance(bullets, dict):
            bullets = [bullets]
        result["bullets"] = bullets

        usage = getattr(response, "usage", None)
        logger.info(
            "  %s — %s tokens in / %s tokens out",
            cfg.claude_model,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
        )

        return result


# ---------------------------------------------------------------------------
# Digest mode — zero Claude calls, headlines + data feeds only
# ---------------------------------------------------------------------------

_MA_KEYWORDS = (
    "merger", "acquisition", "acquire", "acquires", "buyout", "m&a",
    "takeover", "take-private", "deal", "lbo", "spinoff", "divest",
)


def extract_ma_headlines(raw_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull M&A-focused headlines from Finnhub merger feed and keyword matches."""
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for section_headlines in raw_data.get("sections", {}).values():
        for h in section_headlines:
            fp = h.get("fingerprint") or h.get("headline", "")
            if fp in seen:
                continue
            source = h.get("source", "").lower()
            text = f"{h.get('headline', '')} {h.get('summary', '')}".lower()
            if source == "finnhub:merger" or any(k in text for k in _MA_KEYWORDS):
                seen.add(fp)
                items.append(h)
    return items[:15]


def compile_digest(raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Build all briefing sections from routed headlines — no Claude API calls.
    Uses Finnhub, RSS, and other feeds already collected in raw_data.
    """
    result: dict[str, Any] = {}
    for section_key, section_cfg in SECTION_CONFIGS.items():
        result[section_key] = _digest_section(section_key, section_cfg, raw_data)
    return result


def _digest_section(
    section_key: str,
    section_cfg: dict,
    raw_data: dict[str, Any],
) -> dict[str, Any]:
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
        for item in headlines[:8]
    ]

    if headlines:
        lead = headlines[0]
        lead_headline = lead.get("headline", "").rstrip(".")
        lead_summary = (lead.get("summary") or "").strip()
        bottom_line = lead_headline
        parts = [f"Lead story: {lead_headline}."]
        if lead_summary:
            parts.append(lead_summary)
        if len(headlines) > 1:
            parts.append(
                "Also today: "
                + "; ".join(
                    h.get("headline", "") for h in headlines[1:4] if h.get("headline")
                )
                + "."
            )
        narrative = " ".join(parts)
    else:
        narrative = "No major headlines in this area today."
        bottom_line = "Quiet session for this coverage area."

    return {
        "title": section_cfg["title"],
        "color": section_cfg["color"],
        "bottom_line": bottom_line[:280],
        "narrative": narrative[:1400],
        "bullets": bullets,
    }


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
        themes = "; ".join(
            h.get("headline", "") for h in headlines[1:4] if h.get("headline")
        )
        narrative = (
            "AI synthesis was unavailable for this section today, so the items "
            "below are presented without full editorial analysis. The day's lead "
            f"development concerned: {lead.get('headline', '').rstrip('.')}. "
            f"{(lead.get('summary') or '').strip()}"
        ).strip()
        if themes:
            narrative += (
                "\n\nOther threads worth watching in this area today included: "
                f"{themes}. Read these alongside the market data table above for "
                "context until full analysis resumes."
            )
        bottom_line = (
            "Automated analysis was unavailable for this section today; the items "
            "below are raw source headlines rather than interpreted insight."
        )
    else:
        narrative = (
            "No section-specific headlines were available today, and automated "
            f"analysis could not run (reason: {error_msg[:120]})."
        )
        bottom_line = (
            "No data or analysis was available for this section in today's run."
        )

    return {
        "title": section_cfg["title"],
        "color": section_cfg["color"],
        "bottom_line": bottom_line,
        "narrative": narrative[:1400],
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


def _fmt_ticker_line(data: dict) -> str:
    if data.get("value") is None:
        return "N/A"
    chg = data.get("change_pct")
    chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
    direction = data.get("direction", "")
    return f"{_fmt_value(data['value'], data.get('format',''))} ({chg_str}) [{direction}]"


def _append_primary_indices(parts: list, snap: dict) -> None:
    primary = snap.get("primary", {})
    if not primary:
        return
    parts.append("PRIMARY INDICES:")
    for key, data in primary.items():
        if data.get("value") is not None:
            parts.append(f"  {data['label']}: {_fmt_ticker_line(data)}")
    vix = primary.get("vix", {})
    if vix.get("value") is not None:
        level = vix["value"]
        regime = "elevated fear" if level > 25 else "moderate caution" if level > 18 else "complacency/calm"
        parts.append(f"  VIX regime note: {level:.1f} indicates {regime}")
    parts.append("")


def _append_sector_performance(parts: list, snap: dict) -> None:
    sectors = snap.get("sectors", {})
    if not sectors:
        return
    valid = [(k, v) for k, v in sectors.items() if v.get("change_pct") is not None]
    if not valid:
        return
    ranked = sorted(valid, key=lambda x: x[1].get("change_pct", 0), reverse=True)
    parts.append("S&P 500 SECTOR ETF PERFORMANCE (ranked best to worst):")
    for key, data in ranked:
        chg = data.get("change_pct", 0)
        parts.append(f"  {data['label']} ({key.upper()}): {chg:+.2f}%")
    if ranked:
        best = ranked[0][1]
        worst = ranked[-1][1]
        spread = (best.get("change_pct", 0) or 0) - (worst.get("change_pct", 0) or 0)
        parts.append(f"  Sector dispersion (leader-laggard spread): {spread:.2f}pp")
    parts.append("")


def _append_yield_curve(parts: list, snap: dict) -> None:
    rates = snap.get("rates", {})
    if not rates:
        return
    parts.append("YIELD CURVE & FIXED INCOME:")
    order = ["treasury_2y", "treasury_5y", "treasury_10y", "treasury_30y"]
    for key in order:
        data = rates.get(key, {})
        if data.get("value") is not None:
            parts.append(f"  {data['label']}: {_fmt_ticker_line(data)}")
    derived = snap.get("derived", {})
    if derived:
        spread_bps = derived.get("spread_2y10y_bps")
        inverted = derived.get("yield_curve_inverted", False)
        if spread_bps is not None:
            status = "INVERTED (recession signal active)" if inverted else "normal (positive slope)"
            parts.append(f"  2Y-10Y Spread: {spread_bps:+d}bps — curve is {status}")
    parts.append("")


def _append_fx(parts: list, snap: dict) -> None:
    fx = snap.get("fx", {})
    if not fx:
        return
    parts.append("FOREIGN EXCHANGE:")
    for key, data in fx.items():
        if data.get("value") is not None:
            parts.append(f"  {data['label']}: {_fmt_ticker_line(data)}")
    dxy = fx.get("dxy", {})
    if dxy.get("value") is not None:
        chg = dxy.get("change_pct", 0) or 0
        direction = "strengthening (headwind for commodities and EM)" if chg > 0.1 else \
                    "weakening (tailwind for commodities and EM)" if chg < -0.1 else "broadly flat"
        parts.append(f"  DXY note: dollar {direction}")
    parts.append("")


def _append_commodities(parts: list, snap: dict) -> None:
    commodities = snap.get("commodities", {})
    if not commodities:
        return
    parts.append("COMMODITIES:")
    for key, data in commodities.items():
        if data.get("value") is not None:
            parts.append(f"  {data['label']}: {_fmt_ticker_line(data)}")
    parts.append("")


def _append_international(parts: list, snap: dict) -> None:
    intl = snap.get("international", {})
    if not intl:
        return
    parts.append("INTERNATIONAL EQUITY INDICES:")
    for key, data in intl.items():
        if data.get("value") is not None:
            parts.append(f"  {data['label']}: {_fmt_ticker_line(data)}")
    parts.append("")


def _append_crypto(parts: list, snap: dict) -> None:
    crypto = snap.get("crypto", {})
    if not crypto:
        return
    parts.append("CRYPTOCURRENCY:")
    for key, data in crypto.items():
        if data.get("value") is not None:
            parts.append(f"  {data['label']}: {_fmt_ticker_line(data)}")
    parts.append("")
