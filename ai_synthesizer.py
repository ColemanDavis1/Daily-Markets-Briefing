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

from claude_client import (
    UsageLimitReached,
    call_model_json,
    backend_status,
)
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

def _resolve_sources(ids: Any, lookup: dict[str, dict]) -> list[dict]:
    """
    Turn the source ids the model cited into real publisher names and URLs.

    Anything the model returns that is not in the lookup is dropped, so a
    citation can never point at an article that was not in today's pull.
    """
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(ids, list):
        return out
    for raw in ids:
        entry = lookup.get(str(raw).strip().upper())
        if entry and entry.get("url") and entry["url"] not in seen:
            seen.add(entry["url"])
            out.append(entry)
    return out


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
# Packs
#
# One model call per pack rather than per section. This is the difference
# between a run that fits inside a Claude Pro allowance and one that does not:
# 18 sections drafted and verified individually is 36 calls, which exhausted a
# weekly limit mid-run on the first live test. Four packs, each covering
# several sections, costs roughly a tenth of that.
#
# Batching also sends the verified fact sheet four times instead of thirty-six,
# which is where most of the input tokens were going.
# ---------------------------------------------------------------------------

PACKS: list[dict] = [
    {
        "key": "lead",
        "label": "The Desk",
        "sections": ["editor_note", "market_wrap", "rates_fed", "econ_data"],
        "include_calendar": True,
    },
    {
        "key": "coverage",
        "label": "Coverage Groups",
        "sections": list(COVERAGE_GROUPS.keys()),
        "include_calendar": False,
    },
    {
        "key": "product",
        "label": "Product Groups",
        "sections": list(PRODUCT_GROUPS.keys()),
        "include_calendar": False,
    },
    {
        "key": "standing",
        "label": "Forward Look",
        "sections": ["geopolitical", "what_to_watch"],
        "include_calendar": True,
    },
]

# Per-section schema guidance for the two sections that are not plain stories.
_SPECIAL_SCHEMAS: dict[str, str] = {
    "editor_note": (
        'Keys: "headline", "narrative" (the opening, 130 words), "one_thing" '
        '(a single sentence under 30 words the reader could say out loud if '
        'asked what is going on in the markets, specific and quantified), '
        '"source_ids". No interview_question or interview_answer for this one.'
    ),
    "what_to_watch": (
        'Keys: "headline", "catalysts" (an array of 4-5 objects, each with '
        '"what", "when", "consensus", "why", "bull", "bear"), '
        '"interview_question", "interview_answer", "source_ids". '
        'No "narrative" for this one. If no consensus figure is provided in the '
        'inputs, write "no published consensus in today\'s data" rather than '
        'inventing one.'
    ),
}

_STORY_KEYS = (
    'Keys: "headline", "quiet_day" (boolean), "narrative" (paragraphs separated '
    'by \\n\\n), "why_it_matters" (one sentence), "interview_question", '
    '"interview_answer" (3-5 sentences written to be spoken out loud), '
    '"numbers" (array of {"label","value"}), "source_ids".'
)


def _pack_prompt(
    pack: dict,
    plan_by_key: dict[str, dict],
    grouped: dict,
    raw_data: dict,
    fact_sheet: str,
    calendar_text: str,
    earnings_text: str,
) -> tuple[str, dict[str, dict], list[str]]:
    """
    Build one prompt covering every section in the pack.

    Source ids are numbered once across the whole pack so the model has a
    single lookup table and Python can resolve every citation to a real URL.
    """
    lookup: dict[str, dict] = {}
    counter = 1
    blocks: list[str] = []
    included: list[str] = []

    for key in pack["sections"]:
        section = plan_by_key.get(key)
        if not section:
            continue
        included.append(key)

        # Pick the candidate pool this section should choose from.
        if key == "editor_note":
            items = _top_headlines(grouped, limit=14)
        elif key in ("rates_fed", "econ_data"):
            items = _rate_relevant_headlines(raw_data)[:8]
        elif key == "market_wrap":
            items = _top_headlines(grouped, limit=8, prefer=["fig", "tmt"])
        elif key == "what_to_watch":
            items = _top_headlines(grouped, limit=5)
        else:
            items = (grouped.get(key) or [])[:9]

        source_lines: list[str] = []
        for item in items:
            sid = f"S{counter}"
            counter += 1
            lookup[sid] = {
                "title": item.get("headline", ""),
                "url": item.get("url", ""),
                "publisher": item.get("source_name") or item.get("source", ""),
                "published": item.get("published", ""),
            }
            summary = (item.get("summary") or "").strip()
            source_lines.append(
                f"  [{sid}] {item.get('headline', '')}"
                + (f"\n        {summary[:280]}" if summary else "")
                + f"\n        source: {lookup[sid]['publisher']}"
            )

        schema_note = _SPECIAL_SCHEMAS.get(key, _STORY_KEYS)
        mandate = section.get("focus") or _LEAD_MANDATES.get(key, "")

        blocks.append(
            f"""
=== SECTION "{key}" : {section['title']} ===
Target length: about {section['words']} words.
Mandate: {mandate}
{schema_note}
Candidate stories for this desk:
{chr(10).join(source_lines) if source_lines else "  (none in today's pull)"}
"""
        )

    calendars = ""
    if pack.get("include_calendar"):
        calendars = f"""
ECONOMIC CALENDAR (release schedule; consensus only where shown)
{calendar_text}

EARNINGS CALENDAR
{earnings_text}
"""

    prompt = f"""{fact_sheet}
{calendars}
YOUR ASSIGNMENT
Write {len(included)} sections of today's note, listed below. Each section gets
its own entry in the output. Write each one to its own mandate and length, and
select the single most consequential story from that section's candidate list
rather than summarizing the list.

Cite the source ids you actually drew on, per section, in "source_ids".

IF A DESK HAS NO REAL STORY TODAY
Set "quiet_day": true for that section, say so in the first sentence, and spend
the section on standing context: where that group's activity, spreads,
valuations or pipeline currently sit based on the verified facts. Do not
manufacture a story. An honest short section beats invented filler.
{"".join(blocks)}
{_JSON_RULES}

SCHEMA
{{
  "sections": {{
    "<section key exactly as given above>": {{ ...that section's keys... }}
  }}
}}
Include an entry for every one of the {len(included)} sections: {", ".join(included)}."""

    return prompt, lookup, included


_LEAD_MANDATES: dict[str, str] = {
    "editor_note": (
        "The single most important thing a person walking into a markets "
        "conversation this morning needs in their head. Pick one thing, not a "
        "list, and make clear why it outranks everything else on the page."
    ),
    "market_wrap": (
        "Explain the session rather than reciting the tables the reader can "
        "already see. What drove the index moves, whether the move was broad or "
        "narrow, what sector rotation says about risk appetite, whether the "
        "cross-asset picture confirms or contradicts equities, what the "
        "overnight futures and the Asian and European sessions add, and the VIX "
        "in context including what it implies for the issuance window. Note "
        "that cash index levels are the prior session's close, since this goes "
        "out before the US open."
    ),
    "rates_fed": (
        "The most important section in the note and the one an interviewer is "
        "most likely to probe. Work through the shape of the curve and which "
        "end moved; what 2s10s and 3M10Y mean and why anyone watches them; the "
        "funding stack, SOFR against IORB and EFFR, what that spread signals "
        "about reserve scarcity, and what reverse repo balances add; the "
        "decomposition of the 10-year into real yield and breakeven and which "
        "component moved; credit spreads as the risk-appetite gauge and what "
        "they imply for leveraged finance and LBO math specifically; and the "
        "curve-implied policy path with the next FOMC date. State in one clause "
        "that the implied path is derived from the Treasury curve rather than "
        "fed funds futures and will differ modestly from CME FedWatch. Define "
        "SOFR, IORB, OAS, breakeven and term premium on first use: the reader "
        "needs to explain these out loud, not just recognize them."
    ),
    "econ_data": (
        "Lead with whatever printed most recently. For each release that "
        "matters: actual against consensus against prior, any revision, and the "
        "read-through for the Fed's reaction function. Then place it in the "
        "trend: is inflation converging on target or stalling, is the labor "
        "market cooling or cracking?"
    ),
}


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

class Synthesizer:
    def __init__(self) -> None:
        self.degraded: list[str] = []
        self.verified_count = 0
        self.correction_log: list[dict] = []
        self.limit_hit: str = ""

    def synthesize(self, raw_data: dict[str, Any], engine: dict[str, Any]) -> dict[str, Any]:
        fact_sheet = engine.get("fact_sheet", "")
        grouped = raw_data.get("groups") or {}
        plan = build_section_plan()
        plan_by_key = {s["key"]: s for s in plan}

        status = backend_status()
        logger.info("Model backends available: %s", status)
        if not any(status.values()):
            logger.error("No model backend reachable. Shipping the deterministic edition.")
            return compile_data_edition(
                raw_data, engine, reason="no model backend available"
            )

        calendar_text = _calendar_text(raw_data.get("economic_calendar") or [])
        earnings_text = _earnings_text(raw_data.get("earnings_calendar") or [])

        sections: dict[str, dict] = {}

        for index, pack in enumerate(PACKS):
            pack_sections = [k for k in pack["sections"] if k in plan_by_key]
            if not pack_sections:
                continue

            if self.limit_hit:
                # The allowance is gone. Fill the rest deterministically rather
                # than burning minutes on calls that cannot succeed.
                for key in pack_sections:
                    sections[key] = _fallback_section(
                        plan_by_key[key], grouped.get(key, [])
                    )
                    self.degraded.append(key)
                continue

            if index > 0 and cfg.section_delay_sec > 0:
                time.sleep(cfg.section_delay_sec)

            logger.info(
                "Pack %d/%d: %s (%d sections)",
                index + 1, len(PACKS), pack["label"], len(pack_sections),
            )
            try:
                sections.update(
                    self._build_pack(
                        pack, plan_by_key, grouped, raw_data,
                        fact_sheet, calendar_text, earnings_text,
                    )
                )
            except UsageLimitReached as exc:
                self.limit_hit = str(exc)
                logger.error(
                    "Usage limit reached during pack '%s'. Remaining sections "
                    "will ship deterministically.", pack["key"],
                )
                for key in pack_sections:
                    if key not in sections:
                        sections[key] = _fallback_section(
                            plan_by_key[key], grouped.get(key, [])
                        )
                        self.degraded.append(key)
            except Exception as exc:
                logger.error("Pack '%s' failed: %s", pack["key"], exc, exc_info=True)
                for key in pack_sections:
                    if key not in sections:
                        sections[key] = _fallback_section(
                            plan_by_key[key], grouped.get(key, [])
                        )
                        self.degraded.append(key)

        sections["_meta"] = {
            "degraded_sections": self.degraded,
            "verified_count": self.verified_count,
            "corrections": self.correction_log,
            "backends": status,
            "edition": "newsletter",
            "limit_hit": self.limit_hit,
            "packs": len(PACKS),
        }
        return sections

    # ------------------------------------------------------------------

    def _build_pack(
        self,
        pack: dict,
        plan_by_key: dict,
        grouped: dict,
        raw_data: dict,
        fact_sheet: str,
        calendar_text: str,
        earnings_text: str,
    ) -> dict[str, dict]:
        prompt, lookup, included = _pack_prompt(
            pack, plan_by_key, grouped, raw_data,
            fact_sheet, calendar_text, earnings_text,
        )

        data = call_model_json(
            _HOUSE_STYLE, prompt,
            max_tokens=cfg.claude_max_tokens,
            label=f"pack:{pack['key']}",
        )

        if cfg.verify_sections:
            data = self._verify_pack(pack, data, fact_sheet, prompt)

        payload = data.get("sections")
        if not isinstance(payload, dict):
            # Some responses return the sections at the top level.
            payload = {k: v for k, v in data.items() if k in included}

        out: dict[str, dict] = {}
        for key in included:
            section = plan_by_key[key]
            entry = payload.get(key)
            if not isinstance(entry, dict) or not entry:
                logger.warning("  %s missing from pack output. Falling back.", key)
                out[key] = _fallback_section(section, grouped.get(key, []))
                self.degraded.append(key)
                continue

            if key == "editor_note":
                # The pack schema calls the body "narrative"; the editor note
                # shaper expects "note".
                entry.setdefault("note", entry.get("narrative", ""))
                out[key] = _shape_editor_note(section, entry, lookup)
            elif key == "what_to_watch":
                out[key] = _shape_watch(section, entry, lookup)
            else:
                out[key] = _shape_story(section, entry, lookup)

        logger.info(
            "  pack '%s': %d of %d sections written.",
            pack["key"],
            sum(1 for k in included if out.get(k, {}).get("available")),
            len(included),
        )
        return out

    def _verify_pack(self, pack: dict, draft: dict, fact_sheet: str, prompt: str) -> dict:
        import json as _json

        # Reuse the pack prompt's source listing so the checker sees exactly
        # what the writer had available.
        sources_excerpt = prompt.split("YOUR ASSIGNMENT", 1)[-1][:12000]

        verify_prompt = f"""{fact_sheet}

WHAT THE WRITER WAS GIVEN
{sources_excerpt}

DRAFT TO CHECK
{_json.dumps(draft, indent=2)[:60000]}

Return the corrected JSON object with the same structure, plus a "corrections"
array listing each change you made in a few words."""

        try:
            checked = call_model_json(
                _VERIFY_SYSTEM, verify_prompt,
                max_tokens=cfg.claude_max_tokens,
                label=f"pack:{pack['key']} verify",
            )
        except UsageLimitReached:
            logger.warning(
                "  pack '%s': usage limit reached before verification. "
                "Keeping the unverified draft.", pack["key"],
            )
            return draft
        except Exception as exc:
            logger.warning(
                "  pack '%s' verification failed (%s). Keeping the draft.",
                pack["key"], exc,
            )
            return draft

        corrections = checked.pop("corrections", []) or []
        if corrections:
            logger.info(
                "  pack '%s': %d correction(s) applied.", pack["key"], len(corrections)
            )
            self.correction_log.append(
                {"section": pack["key"], "items": corrections}
            )

        if not isinstance(checked, dict) or not checked.get("sections"):
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
