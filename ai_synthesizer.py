"""
Synthesis layer.

Turns the aggregated sources and the deterministic market engine into the
newsletter's written sections.

Division of labor:
  market_engine.py  produces every number and a rule-based reading of it
  this module       produces the explanation, the interview framing, and the
                    editorial judgment about which story matters

The model is handed the computed fact sheet as authoritative context and is
instructed that it may explain those figures but must not introduce a number
that is not either in the fact sheet or in the day's source articles. A second
verification pass then checks the draft against the same fact sheet and strips
anything unsupported.

Every section degrades independently. If a model call fails, that section
falls back to a deterministic summary with live source links rather than
blocking the run or shipping invented content.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from claude_client import ModelUnavailable, call_model_json, backend_status
from config import get_config
from groups import ALL_GROUPS, COVERAGE_GROUPS, GROUP_ORDER, PRODUCT_GROUPS

logger = logging.getLogger(__name__)
cfg = get_config()


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------

_HOUSE_STYLE = """You write the morning markets note for an investment banking desk.

Your reader is preparing for investment banking interviews. They will be asked
"what's going on in the markets?" and "walk me through a deal you've been
following." Your job is to make them able to answer those questions with
specifics and with the reasoning behind them.

VOICE
Write in clean, declarative prose. Confident, never breathless. You are
explaining to a sharp person who does not yet have the reps, so define a term
briefly the first time you use it, then use it normally. No hedging filler, no
"it remains to be seen," no restating the headline back to the reader.

THE ONE RULE THAT MATTERS
Explain the mechanism. Never state that something moved without stating why it
moved and what it implies. "Yields fell 8bp" is worthless. "Yields fell 8bp
after the soft payrolls print, which lowers the discount rate applied to future
earnings and mechanically lifts long-duration growth equities" is the standard.

NUMBERS
You are given a VERIFIED MARKET FACTS block. Those figures are computed from
primary sources and are authoritative. You may cite any of them. You may also
cite figures stated explicitly in the source articles provided. You may not
introduce any other number from memory, and you may not adjust, round
differently, or extrapolate the verified figures. If you want to make a point
that needs a number you do not have, make the point qualitatively instead.

NEVER
Never invent a company, a deal, a price, a person, or a quote. Never describe an
event that is not in the provided sources. If the sources are thin, say so
plainly and write about the standing context instead. A short honest section is
correct; a padded invented one is a failure."""


_JSON_RULES = """OUTPUT
Return exactly one valid JSON object and nothing else. No markdown fences, no
preamble, no trailing commentary. Use \\n\\n between paragraphs inside string
values. Do not include any key not in the schema."""


# ---------------------------------------------------------------------------
# Section plan
# ---------------------------------------------------------------------------

def _lead_sections() -> list[dict]:
    return [
        {
            "key": "editor_note",
            "title": "The Lead",
            "kind": "lead",
            "accent": "#0F172A",
            "short": "LED",
            "words": 130,
        },
        {
            "key": "market_wrap",
            "title": "Market Wrap",
            "subtitle": "Where Everything Closed and Why",
            "kind": "lead",
            "accent": "#0369A1",
            "short": "MKT",
            "words": 380,
        },
        {
            "key": "rates_fed",
            "title": "Rates, the Fed & Funding",
            "subtitle": "The Curve, SOFR and the Policy Path",
            "kind": "lead",
            "accent": "#065F46",
            "short": "RTS",
            "words": 520,
        },
        {
            "key": "econ_data",
            "title": "Economic Data",
            "subtitle": "Prints, Revisions and the Fed Read",
            "kind": "lead",
            "accent": "#7C2D12",
            "short": "ECO",
            "words": 230,
        },
    ]


def build_section_plan() -> list[dict]:
    """Ordered list of every section the newsletter will attempt to produce."""
    plan = _lead_sections()

    for key in GROUP_ORDER:
        if key in cfg.skip_groups:
            continue
        meta = ALL_GROUPS[key]
        words = (
            cfg.coverage_story_words if key in COVERAGE_GROUPS
            else cfg.product_story_words if key in PRODUCT_GROUPS
            else 220
        )
        plan.append({
            "key": key,
            "title": meta["title"],
            "subtitle": meta.get("subtitle", ""),
            "kind": meta.get("kind", "coverage"),
            "accent": meta.get("accent", "#334155"),
            "short": meta.get("short", key[:3].upper()),
            "desk_note": meta.get("desk_note", ""),
            "focus": meta.get("focus", ""),
            "words": 260 if key == "what_to_watch" else words,
        })

    return plan


# ---------------------------------------------------------------------------
# Source formatting
# ---------------------------------------------------------------------------

def _format_sources(items: list[dict], limit: int = 12) -> tuple[str, dict[str, dict]]:
    """
    Number the candidate stories so the model can cite them by id.

    Returns the prompt text and a lookup used to resolve the ids the model
    returns into real publisher names and URLs. Citations are therefore always
    links to articles that actually exist in today's pull.
    """
    lookup: dict[str, dict] = {}
    lines: list[str] = []

    for i, item in enumerate(items[:limit], start=1):
        sid = f"S{i}"
        lookup[sid] = {
            "title": item.get("headline", ""),
            "url": item.get("url", ""),
            "publisher": item.get("source_name") or item.get("source", ""),
            "published": item.get("published", ""),
        }
        summary = (item.get("summary") or "").strip()
        lines.append(
            f"[{sid}] {item.get('headline', '')}"
            + (f"\n      {summary[:400]}" if summary else "")
            + f"\n      source: {lookup[sid]['publisher']}"
        )

    return ("\n".join(lines) if lines else "(no qualifying articles in today's pull)"), lookup


def _resolve_sources(ids: Any, lookup: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(ids, list):
        return out
    for raw in ids:
        sid = str(raw).strip().upper()
        entry = lookup.get(sid)
        if entry and entry.get("url") and entry["url"] not in seen:
            seen.add(entry["url"])
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_STORY_SCHEMA = """{
  "headline": "the story in one specific sentence, naming the actors and the number that matters",
  "quiet_day": false,
  "narrative": "%(words)d words, 2-3 paragraphs separated by \\n\\n. Mechanism first: what happened, why it happened, who it affects, what it implies next.",
  "why_it_matters": "one sentence on the read-through beyond this single story",
  "interview_question": "the question an interviewer would actually ask coming out of this story, phrased the way a banker would ask it",
  "interview_answer": "3-5 sentences, written to be spoken out loud, that answer that question completely. Start with the direct answer, then the supporting numbers, then the implication. No preamble.",
  "numbers": [{"label": "what it is", "value": "the figure with units"}],
  "source_ids": ["S1", "S3"]
}"""


def _story_prompt(section: dict, sources_text: str, fact_sheet: str, extra: str = "") -> str:
    words = section.get("words", 200)
    quiet_clause = (
        "\n\nIF THERE IS NO REAL STORY TODAY\n"
        "Set \"quiet_day\": true, say in the first sentence that the desk was "
        "quiet, and then spend the section on standing context: where this "
        "group's activity, spreads, valuations or pipeline currently sit based "
        "on the verified facts and whatever the sources do support. Do not "
        "manufacture a story. A reader who is told the desk was quiet and given "
        "real context is better served than one given filler."
    )

    return f"""{fact_sheet}

TODAY'S CANDIDATE STORIES FOR THIS DESK
{sources_text}

YOUR ASSIGNMENT
Write the {section['title']} section of today's note.

EDITORIAL MANDATE
{section.get('focus', '')}
{extra}

Select the single most consequential story from the candidates above and write
about that one. Do not summarize the list. Cite the ids of every source you
drew on in "source_ids".{quiet_clause}

{_JSON_RULES}

SCHEMA
{_STORY_SCHEMA % {"words": words}}"""


def _editor_note_prompt(fact_sheet: str, headlines_text: str) -> str:
    return f"""{fact_sheet}

TODAY'S MOST SIGNIFICANT HEADLINES ACROSS ALL DESKS
{headlines_text}

YOUR ASSIGNMENT
Write the opening of today's note: the single most important thing a person
walking into a markets conversation this morning needs to have in their head.

Pick one thing. Not a list. It might be a data print, a policy signal, a move
in the curve, or a landmark deal. Justify the choice implicitly by explaining
why it dominates everything else on the page.

{_JSON_RULES}

SCHEMA
{{
  "headline": "a specific, declarative sentence naming the day's dominant story",
  "note": "130 words, 1-2 paragraphs separated by \\n\\n, explaining what happened and why it outranks everything else today",
  "one_thing": "a single sentence, under 30 words, that the reader could say out loud if asked 'what's going on in the markets?' Make it specific and quantified.",
  "source_ids": ["S1"]
}}"""


def _market_wrap_prompt(fact_sheet: str, sources_text: str) -> str:
    return f"""{fact_sheet}

RELEVANT HEADLINES
{sources_text}

YOUR ASSIGNMENT
Write the Market Wrap. The reader can see the price tables, so do not recite
them. Explain the session.

Cover, in whatever order the day justifies:
- What actually drove the index moves, and whether the move was broad or narrow
- What sector rotation says about risk appetite right now
- Whether the cross-asset picture (rates, dollar, gold, copper, oil) confirms
  or contradicts what equities did, and flag any divergence explicitly
- What the overnight futures and the Asian and European sessions add
- The VIX level in context, including what it implies for the issuance window

Note for the reader that cash index levels are the prior session's close, since
this note goes out before the US open.

{_JSON_RULES}

SCHEMA
{_STORY_SCHEMA % {"words": 380}}"""


def _rates_prompt(fact_sheet: str, sources_text: str) -> str:
    return f"""{fact_sheet}

RELEVANT HEADLINES
{sources_text}

YOUR ASSIGNMENT
Write the Rates, the Fed and Funding section. This is the most important
section in the note and the one an interviewer is most likely to probe. It
should be the longest.

Work through, explaining the mechanism at each step:
- The shape of the curve, what changed today, and which end drove it
- What the 2s10s and 3M10Y spreads mean and why anyone watches them
- The funding stack: SOFR against IORB and EFFR, what the spread signals about
  reserve scarcity, and what reverse repo balances add to that picture
- The decomposition of the 10-year into real yield and breakeven, and which
  component moved
- Credit spreads as the risk-appetite gauge, and what they imply for the
  leveraged finance and LBO market specifically
- The curve-implied policy path, the next FOMC date, and any Fed commentary in
  today's sources

Be explicit that the implied easing path is derived from the Treasury curve
rather than from fed funds futures, and that it will differ modestly from
CME FedWatch. Say that in one clause, not a paragraph.

Define every piece of jargon on first use. SOFR, IORB, OAS, breakeven, term
premium: the reader needs to be able to explain these out loud, not just
recognize them.

{_JSON_RULES}

SCHEMA
{_STORY_SCHEMA % {"words": 520}}"""


def _econ_prompt(fact_sheet: str, sources_text: str, calendar_text: str) -> str:
    return f"""{fact_sheet}

RELEVANT HEADLINES
{sources_text}

ECONOMIC CALENDAR (recent and upcoming, with consensus where available)
{calendar_text}

YOUR ASSIGNMENT
Write the Economic Data section. Lead with whatever printed most recently.

For each release that matters: actual against consensus against prior, any
revision to the prior month, and the read-through for the Fed's reaction
function. Then place it in the trend: is inflation converging on target or
stalling, is the labor market cooling or cracking?

{_JSON_RULES}

SCHEMA
{_STORY_SCHEMA % {"words": 230}}"""


def _watch_prompt(fact_sheet: str, calendar_text: str, earnings_text: str, sources_text: str) -> str:
    return f"""{fact_sheet}

ECONOMIC CALENDAR
{calendar_text}

EARNINGS CALENDAR
{earnings_text}

RELEVANT HEADLINES
{sources_text}

YOUR ASSIGNMENT
Identify the four or five most consequential catalysts in the next 24 to 72
hours. Use the calendars above plus the FOMC date in the verified facts. Do not
invent an event or a consensus figure that is not provided.

{_JSON_RULES}

SCHEMA
{{
  "headline": "one sentence framing what this stretch is really a test of",
  "quiet_day": false,
  "catalysts": [
    {{
      "what": "the event, named specifically",
      "when": "day and time if known, otherwise the day",
      "consensus": "the expected figure if provided in the inputs, otherwise 'no published consensus in today's data'",
      "why": "one sentence on which market narrative this confirms or breaks",
      "bull": "what a stronger-than-expected outcome would do and to what",
      "bear": "what a weaker-than-expected outcome would do and to what"
    }}
  ],
  "interview_question": "the forward-looking question an interviewer would ask about the week ahead",
  "interview_answer": "3-5 spoken-word sentences naming the catalysts that matter and what you are watching for",
  "source_ids": []
}}"""


_VERIFY_SYSTEM = """You are the fact checker on a markets desk. You receive a
verified facts block and a draft section. Your only job is accuracy.

Check every number, name, and claim in the draft against the verified facts and
the source articles. Then return the corrected draft.

Rules:
- A figure that contradicts the verified facts must be corrected to the
  verified value.
- A figure that appears in neither the verified facts nor the source articles
  must be removed, and the sentence rewritten so it still reads naturally
  without it.
- A company, deal, or event not present in the sources must be deleted entirely.
- Do not improve the prose, change the angle, or add anything. If a passage is
  accurate, return it byte-for-byte unchanged.
- Keep the exact same JSON schema as the draft.

Return the corrected JSON object plus a "corrections" array listing each change
you made in a few words. Empty array if the draft was clean."""


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

class Synthesizer:
    def __init__(self) -> None:
        self.degraded: list[str] = []
        self.verified_count = 0
        self.correction_log: list[dict] = []

    def synthesize(self, raw_data: dict[str, Any], engine: dict[str, Any]) -> dict[str, Any]:
        fact_sheet = engine.get("fact_sheet", "")
        grouped = raw_data.get("groups") or {}
        plan = build_section_plan()

        status = backend_status()
        logger.info("Model backends available: %s", status)
        if not any(status.values()):
            logger.error(
                "No model backend reachable. Shipping the deterministic edition."
            )
            return compile_data_edition(raw_data, engine, reason="no model backend available")

        sections: dict[str, dict] = {}
        calendar_text = _calendar_text(raw_data.get("economic_calendar") or [])
        earnings_text = _earnings_text(raw_data.get("earnings_calendar") or [])

        for index, section in enumerate(plan):
            key = section["key"]
            if index > 0 and cfg.section_delay_sec > 0:
                time.sleep(cfg.section_delay_sec)

            logger.info("Section %d/%d: %s", index + 1, len(plan), section["title"])
            try:
                sections[key] = self._build_section(
                    section, raw_data, grouped, fact_sheet,
                    calendar_text, earnings_text,
                )
            except ModelUnavailable as exc:
                logger.error("  %s: no backend (%s). Using deterministic fallback.", key, exc)
                self.degraded.append(key)
                sections[key] = _fallback_section(section, grouped.get(key, []))
            except Exception as exc:
                logger.error("  %s failed: %s", key, exc, exc_info=True)
                self.degraded.append(key)
                sections[key] = _fallback_section(section, grouped.get(key, []))

        sections["_meta"] = {
            "degraded_sections": self.degraded,
            "verified_count": self.verified_count,
            "corrections": self.correction_log,
            "backends": status,
            "edition": "newsletter",
        }
        return sections

    # ------------------------------------------------------------------

    def _build_section(
        self,
        section: dict,
        raw_data: dict,
        grouped: dict,
        fact_sheet: str,
        calendar_text: str,
        earnings_text: str,
    ) -> dict:
        key = section["key"]

        if key == "editor_note":
            items = _top_headlines(grouped, limit=18)
            sources_text, lookup = _format_sources(items, limit=18)
            prompt = _editor_note_prompt(fact_sheet, sources_text)
            data = self._call(section, prompt, fact_sheet, sources_text)
            return _shape_editor_note(section, data, lookup)

        if key == "market_wrap":
            items = _top_headlines(grouped, limit=10, prefer=["fig", "tmt"])
            sources_text, lookup = _format_sources(items, limit=10)
            prompt = _market_wrap_prompt(fact_sheet, sources_text)
            data = self._call(section, prompt, fact_sheet, sources_text)
            return _shape_story(section, data, lookup)

        if key == "rates_fed":
            items = _rate_relevant_headlines(raw_data)
            sources_text, lookup = _format_sources(items, limit=10)
            prompt = _rates_prompt(fact_sheet, sources_text)
            data = self._call(section, prompt, fact_sheet, sources_text)
            return _shape_story(section, data, lookup)

        if key == "econ_data":
            items = _rate_relevant_headlines(raw_data)
            sources_text, lookup = _format_sources(items, limit=8)
            prompt = _econ_prompt(fact_sheet, sources_text, calendar_text)
            data = self._call(section, prompt, fact_sheet, sources_text)
            return _shape_story(section, data, lookup)

        if key == "what_to_watch":
            items = _top_headlines(grouped, limit=6)
            sources_text, lookup = _format_sources(items, limit=6)
            prompt = _watch_prompt(fact_sheet, calendar_text, earnings_text, sources_text)
            data = self._call(section, prompt, fact_sheet, sources_text)
            return _shape_watch(section, data, lookup)

        # Coverage and product groups
        items = grouped.get(key, [])
        sources_text, lookup = _format_sources(items, limit=12)
        prompt = _story_prompt(section, sources_text, fact_sheet)
        data = self._call(section, prompt, fact_sheet, sources_text)
        return _shape_story(section, data, lookup)

    # ------------------------------------------------------------------

    def _call(self, section: dict, prompt: str, fact_sheet: str, sources_text: str) -> dict:
        data = call_model_json(
            _HOUSE_STYLE,
            prompt,
            max_tokens=cfg.claude_max_tokens,
            label=section["key"],
        )

        if cfg.verify_sections:
            data = self._verify(section, data, fact_sheet, sources_text)

        return data

    def _verify(self, section: dict, draft: dict, fact_sheet: str, sources_text: str) -> dict:
        import json as _json

        prompt = f"""{fact_sheet}

SOURCE ARTICLES AVAILABLE TO THE WRITER
{sources_text}

DRAFT SECTION TO CHECK
{_json.dumps(draft, indent=2)}

Return the corrected JSON object with the same keys, plus a "corrections" array."""

        try:
            checked = call_model_json(
                _VERIFY_SYSTEM,
                prompt,
                max_tokens=cfg.claude_max_tokens,
                label=f"{section['key']} verify",
            )
        except Exception as exc:
            logger.warning("  %s verification failed (%s). Keeping the draft.", section["key"], exc)
            return draft

        corrections = checked.pop("corrections", []) or []
        if corrections:
            logger.info("  %s: %d correction(s) applied.", section["key"], len(corrections))
            self.correction_log.append({"section": section["key"], "items": corrections})

        # A verifier that returns something structurally broken is not trusted.
        if not isinstance(checked, dict) or not checked:
            return draft
        self.verified_count += 1
        return checked


# ---------------------------------------------------------------------------
# Shaping model output into render-ready sections
# ---------------------------------------------------------------------------

def _base_section(section: dict) -> dict:
    return {
        "key": section["key"],
        "title": section["title"],
        "subtitle": section.get("subtitle", ""),
        "kind": section.get("kind", "coverage"),
        "accent": section.get("accent", "#334155"),
        "short": section.get("short", ""),
        "desk_note": section.get("desk_note", ""),
    }


def _paragraphs(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    normalized = text.replace("\r\n", "\n").strip()
    if "\n\n" not in normalized and "\n" in normalized:
        normalized = "\n\n".join(p.strip() for p in normalized.split("\n") if p.strip())
    return [p.strip() for p in normalized.split("\n\n") if p.strip()]


def _clean_numbers(raw: Any) -> list[dict]:
    out = []
    if isinstance(raw, list):
        for item in raw[:6]:
            if isinstance(item, dict) and item.get("label") and item.get("value"):
                out.append({
                    "label": str(item["label"])[:60],
                    "value": str(item["value"])[:60],
                })
    return out


def _shape_story(section: dict, data: dict, lookup: dict) -> dict:
    out = _base_section(section)
    out.update({
        "headline": str(data.get("headline", "")).strip(),
        "paragraphs": _paragraphs(data.get("narrative")),
        "why_it_matters": str(data.get("why_it_matters", "")).strip(),
        "interview_question": str(data.get("interview_question", "")).strip(),
        "interview_answer": str(data.get("interview_answer", "")).strip(),
        "numbers": _clean_numbers(data.get("numbers")),
        "sources": _resolve_sources(data.get("source_ids"), lookup),
        "quiet_day": bool(data.get("quiet_day")),
        "catalysts": [],
        "available": True,
    })
    return out


def _shape_editor_note(section: dict, data: dict, lookup: dict) -> dict:
    out = _base_section(section)
    out.update({
        "headline": str(data.get("headline", "")).strip(),
        "paragraphs": _paragraphs(data.get("note")),
        "one_thing": str(data.get("one_thing", "")).strip(),
        "sources": _resolve_sources(data.get("source_ids"), lookup),
        "interview_question": "",
        "interview_answer": "",
        "numbers": [],
        "catalysts": [],
        "quiet_day": False,
        "available": True,
    })
    return out


def _shape_watch(section: dict, data: dict, lookup: dict) -> dict:
    catalysts = []
    raw = data.get("catalysts")
    if isinstance(raw, list):
        for item in raw[:6]:
            if not isinstance(item, dict) or not item.get("what"):
                continue
            catalysts.append({
                "what": str(item.get("what", "")).strip(),
                "when": str(item.get("when", "")).strip(),
                "consensus": str(item.get("consensus", "")).strip(),
                "why": str(item.get("why", "")).strip(),
                "bull": str(item.get("bull", "")).strip(),
                "bear": str(item.get("bear", "")).strip(),
            })

    out = _base_section(section)
    out.update({
        "headline": str(data.get("headline", "")).strip(),
        "paragraphs": [],
        "catalysts": catalysts,
        "interview_question": str(data.get("interview_question", "")).strip(),
        "interview_answer": str(data.get("interview_answer", "")).strip(),
        "numbers": [],
        "sources": _resolve_sources(data.get("source_ids"), lookup),
        "quiet_day": not catalysts,
        "available": True,
    })
    return out


def _fallback_section(section: dict, items: list[dict]) -> dict:
    """
    Deterministic stand-in when a model call fails.

    Ships real links rather than prose so the section is still usable and
    visibly marked as unwritten.
    """
    out = _base_section(section)
    sources = [
        {
            "title": i.get("headline", ""),
            "url": i.get("url", ""),
            "publisher": i.get("source_name") or i.get("source", ""),
        }
        for i in items[:5] if i.get("url")
    ]
    out.update({
        "headline": (
            items[0].get("headline", "") if items
            else f"No {section['title']} narrative available this morning."
        ),
        "paragraphs": [
            "Written analysis was unavailable for this desk this morning. The "
            "headlines the desk pulled are linked below so the story can still "
            "be picked up directly from the source."
        ],
        "why_it_matters": "",
        "interview_question": "",
        "interview_answer": "",
        "numbers": [],
        "catalysts": [],
        "sources": sources,
        "quiet_day": False,
        "available": False,
    })
    return out


# ---------------------------------------------------------------------------
# Deterministic edition
# ---------------------------------------------------------------------------

def compile_data_edition(
    raw_data: dict[str, Any], engine: dict[str, Any], reason: str = ""
) -> dict[str, Any]:
    """
    Numbers-only edition.

    Used in data mode and as the last-resort fallback. Every rate, spread and
    index level is still present with its rule-based interpretation, because
    all of that is computed rather than generated. Only the written stories are
    missing.
    """
    sections: dict[str, dict] = {}
    grouped = raw_data.get("groups") or {}

    reads = [
        ("market_wrap", "Market Wrap", "#0369A1", "MKT", engine.get("equity", {}).get("read", "")),
        ("rates_fed", "Rates, the Fed & Funding", "#065F46", "RTS", " ".join(filter(None, [
            engine.get("curve", {}).get("read", ""),
            engine.get("funding", {}).get("read", ""),
            engine.get("inflation", {}).get("read", ""),
            engine.get("credit", {}).get("read", ""),
            engine.get("policy", {}).get("read", ""),
        ]))),
        ("econ_data", "Economic Data", "#7C2D12", "ECO", engine.get("cross_asset", {}).get("read", "")),
    ]

    for key, title, accent, short, read in reads:
        sections[key] = {
            "key": key, "title": title, "subtitle": "", "kind": "lead",
            "accent": accent, "short": short, "desk_note": "",
            "headline": "", "paragraphs": _paragraphs(read),
            "why_it_matters": "", "interview_question": "", "interview_answer": "",
            "numbers": [], "catalysts": [], "sources": [],
            "quiet_day": False, "available": bool(read),
        }

    for key in GROUP_ORDER:
        if key in cfg.skip_groups or key == "what_to_watch":
            continue
        meta = ALL_GROUPS[key]
        sections[key] = _fallback_section(
            {
                "key": key, "title": meta["title"],
                "subtitle": meta.get("subtitle", ""),
                "kind": meta.get("kind", "coverage"),
                "accent": meta.get("accent", "#334155"),
                "short": meta.get("short", ""),
                "desk_note": meta.get("desk_note", ""),
            },
            grouped.get(key, []),
        )

    sections["_meta"] = {
        "degraded_sections": list(sections.keys()),
        "verified_count": 0,
        "corrections": [],
        "backends": backend_status(),
        "edition": "data",
        "reason": reason,
    }
    return sections


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _top_headlines(grouped: dict, limit: int = 15, prefer: list[str] | None = None) -> list[dict]:
    """Highest-confidence story from each group, best first, deduplicated."""
    pool: list[dict] = []
    seen: set[str] = set()

    ordered_keys = (prefer or []) + [k for k in GROUP_ORDER if k not in (prefer or [])]

    # Round-robin across desks so one noisy group cannot crowd out the rest.
    for depth in range(3):
        for key in ordered_keys:
            items = grouped.get(key) or []
            if depth < len(items):
                item = items[depth]
                fp = item.get("fingerprint", "")
                if fp and fp not in seen:
                    seen.add(fp)
                    pool.append(item)
    return pool[:limit]


_RATE_TERMS = (
    "fed", "fomc", "powell", "rate", "yield", "treasury", "inflation", "cpi",
    "pce", "payroll", "jobless", "gdp", "sofr", "repo", "bond", "curve",
    "basis point", "monetary", "central bank", "ecb", "boj",
)


def _rate_relevant_headlines(raw_data: dict) -> list[dict]:
    out = []
    for item in raw_data.get("all_headlines") or []:
        text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        if any(term in text for term in _RATE_TERMS):
            out.append(item)
    return out[:14]


def _calendar_text(events: list[dict]) -> str:
    if not events:
        return "(no economic calendar data available today)"
    lines = []
    for e in events[:14]:
        parts = [
            str(e.get("time", ""))[:16],
            str(e.get("country", "")),
            str(e.get("event", "")),
        ]
        for label, field in (("actual", "actual"), ("consensus", "estimate"), ("prior", "prev")):
            value = e.get(field)
            if value not in (None, ""):
                parts.append(f"{label} {value}")
        impact = e.get("impact")
        if impact:
            parts.append(f"impact {impact}")
        lines.append("  " + " | ".join(p for p in parts if p))
    return "\n".join(lines)


def _earnings_text(events: list[dict]) -> str:
    if not events:
        return "(no earnings calendar data available today)"
    lines = []
    for e in events[:16]:
        symbol = e.get("symbol", "")
        if not symbol:
            continue
        bits = [f"  {symbol}", str(e.get("date", ""))]
        eps = e.get("epsEstimate")
        rev = e.get("revenueEstimate")
        if eps not in (None, ""):
            bits.append(f"EPS est {eps}")
        if rev not in (None, ""):
            try:
                bits.append(f"rev est ${float(rev) / 1e9:.2f}B")
            except (TypeError, ValueError):
                pass
        hour = e.get("hour")
        if hour:
            bits.append({"bmo": "before open", "amc": "after close"}.get(hour, str(hour)))
        lines.append(" | ".join(bits))
    return "\n".join(lines) if lines else "(no earnings calendar data available today)"


# Backwards-compatible alias for the previous entry point name.
AISynthesizer = Synthesizer
