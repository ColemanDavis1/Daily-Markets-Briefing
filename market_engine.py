"""
Deterministic market engine.

Every hard number in the newsletter is computed here, in Python, from the
aggregated source data. The model never generates a figure: it receives this
module's output as an established fact sheet and its job is to explain the
mechanism behind the numbers, not to produce them.

That split is the accuracy guarantee. If a figure appears in the email, it was
either pulled from FRED / Stooq / Finnhub or arithmetically derived from a
pulled value, and the derivation is stated.

Produces:
  key_numbers    the "Know These Cold" block
  curve          full Treasury curve with daily bp changes
  funding        SOFR / EFFR / IORB / RRP with a funding-stress read
  inflation      breakevens and the real-yield decomposition
  credit         HY and IG OAS with 1-year percentile context
  policy         target range, next FOMC, curve-implied easing path
  equity         index levels, breadth, sector rotation
  cross_asset    FX, commodities, crypto
  fact_sheet     plain-text digest injected into every model call
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# FOMC calendar
#
# Decision days (the second day of each two-day meeting, when the statement is
# released). Verified against federalreserve.gov/monetarypolicy/fomccalendars.htm
# on 2026-08-22.
#
# The Fed notes each date is tentative until confirmed at the preceding meeting,
# so re-verify annually. If the list runs out, the policy block degrades to
# "next meeting date unavailable" rather than guessing.
# ---------------------------------------------------------------------------
FOMC_DECISION_DATES: list[date] = [
    # 2026
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
    # 2027
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 4, 28),
    date(2027, 6, 9),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 10, 27),
    date(2027, 12, 8),
]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _f(value: Any, decimals: int = 2) -> str:
    """Format a number, or return an em-free placeholder when absent."""
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _signed(value: Any, decimals: int = 2, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{v:+,.{decimals}f}{suffix}"


def _bp(value: Any) -> str:
    """Format a percentage-point delta as basis points."""
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:+,.0f}bp"
    except (TypeError, ValueError):
        return "n/a"


def _tone(value: Any, invert: bool = False) -> str:
    """up / down / flat, for color coding. invert=True for risk gauges."""
    if value is None:
        return "flat"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "flat"
    if abs(v) < 0.005:
        return "flat"
    positive = v > 0
    if invert:
        positive = not positive
    return "up" if positive else "down"


def _macro(macro: dict, key: str) -> dict:
    return macro.get(key) or {}


def _val(macro: dict, key: str) -> float | None:
    v = _macro(macro, key).get("value")
    return float(v) if isinstance(v, (int, float)) else None


def _prev(macro: dict, key: str) -> float | None:
    v = _macro(macro, key).get("prev_value")
    return float(v) if isinstance(v, (int, float)) else None


def _delta(macro: dict, key: str) -> float | None:
    cur, prev = _val(macro, key), _prev(macro, key)
    if cur is None or prev is None:
        return None
    return round(cur - prev, 4)


def _quote(snapshot: dict, group: str, key: str) -> dict:
    return (snapshot.get(group) or {}).get(key) or {}


# ---------------------------------------------------------------------------
# Treasury curve
# ---------------------------------------------------------------------------

_CURVE_POINTS: list[tuple[str, str]] = [
    ("3M", "ust_3m"),
    ("6M", "ust_6m"),
    ("1Y", "ust_1y"),
    ("2Y", "ust_2y"),
    ("5Y", "ust_5y"),
    ("10Y", "ust_10y"),
    ("30Y", "ust_30y"),
]


def build_curve(macro: dict) -> dict:
    """Full Treasury curve with daily changes, spreads, and a shape read."""
    points = []
    for label, key in _CURVE_POINTS:
        value = _val(macro, key)
        if value is None:
            continue
        change = _delta(macro, key)
        points.append({
            "label": label,
            "value": value,
            "display": f"{value:.2f}%",
            "change_bp": round(change * 100) if change is not None else None,
            "change_display": _bp(change),
            "tone": _tone(change),
            "as_of": _macro(macro, key).get("date", ""),
        })

    t2 = _val(macro, "ust_2y")
    t10 = _val(macro, "ust_10y")
    t3m = _val(macro, "ust_3m")
    t30 = _val(macro, "ust_30y")

    spread_2s10s = round((t10 - t2) * 100) if (t10 is not None and t2 is not None) else None
    spread_3m10y = round((t10 - t3m) * 100) if (t10 is not None and t3m is not None) else None
    spread_5s30s = None
    t5 = _val(macro, "ust_5y")
    if t30 is not None and t5 is not None:
        spread_5s30s = round((t30 - t5) * 100)

    # Day-over-day change in the 2s10s tells you which end of the curve moved.
    d2 = _delta(macro, "ust_2y")
    d10 = _delta(macro, "ust_10y")
    twos_tens_chg = None
    if d2 is not None and d10 is not None:
        twos_tens_chg = round((d10 - d2) * 100)

    read = _curve_read(spread_2s10s, spread_3m10y, twos_tens_chg, d2, d10)

    return {
        "points": points,
        "spread_2s10s_bp": spread_2s10s,
        "spread_3m10y_bp": spread_3m10y,
        "spread_5s30s_bp": spread_5s30s,
        "spread_2s10s_change_bp": twos_tens_chg,
        "inverted_2s10s": spread_2s10s is not None and spread_2s10s < 0,
        "inverted_3m10y": spread_3m10y is not None and spread_3m10y < 0,
        "read": read,
    }


def _curve_read(
    s2s10s: int | None,
    s3m10y: int | None,
    chg: int | None,
    d2: float | None,
    d10: float | None,
) -> str:
    """Rule-based interpretation of curve shape and the day's move."""
    if s2s10s is None:
        return "Treasury curve data unavailable for this session."

    parts: list[str] = []

    if s2s10s < 0:
        parts.append(
            f"The 2s10s spread is inverted at {s2s10s}bp, meaning 2-year yields "
            "exceed 10-year yields. Inversion has historically preceded "
            "recessions, though with long and variable lead times."
        )
    elif s2s10s < 25:
        parts.append(
            f"The 2s10s spread sits at just {s2s10s}bp, a flat curve that leaves "
            "little compensation for holding duration and signals a market "
            "expecting slower growth ahead."
        )
    elif s2s10s < 100:
        parts.append(
            f"The 2s10s spread stands at {s2s10s}bp, a normally positive but "
            "historically modest slope."
        )
    else:
        parts.append(
            f"The 2s10s spread has widened to {s2s10s}bp, a steep curve that "
            "typically reflects expected policy easing at the front end, higher "
            "term premium at the long end, or both."
        )

    if s3m10y is not None:
        if s3m10y < 0:
            parts.append(
                f"The 3M10Y spread, the version the New York Fed uses in its "
                f"recession model, is also inverted at {s3m10y}bp."
            )
        else:
            parts.append(f"The 3M10Y spread is {s3m10y}bp.")

    # Classify the day's move into the four canonical curve regimes.
    if chg is not None and d2 is not None and d10 is not None:
        if abs(chg) < 2:
            parts.append("The curve was broadly unchanged on the session.")
        else:
            steepening = chg > 0
            rates_falling = (d2 + d10) < 0
            if steepening and rates_falling:
                regime = (
                    "a bull steepening: yields fell across the curve but the "
                    "front end fell more, the classic signature of the market "
                    "pulling forward rate cuts"
                )
            elif steepening and not rates_falling:
                regime = (
                    "a bear steepening: yields rose across the curve but the "
                    "long end rose more, usually a term premium or fiscal "
                    "supply story rather than a policy story"
                )
            elif not steepening and rates_falling:
                regime = (
                    "a bull flattening: yields fell with the long end falling "
                    "more, typically a growth-scare signal"
                )
            else:
                regime = (
                    "a bear flattening: yields rose with the front end rising "
                    "more, consistent with the market pricing out cuts"
                )
            parts.append(
                f"The curve moved {abs(chg)}bp {'steeper' if steepening else 'flatter'}, "
                f"{regime}."
            )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Funding markets
# ---------------------------------------------------------------------------

def build_funding(macro: dict) -> dict:
    """SOFR, EFFR, IORB and reverse repo, with a funding-stress read."""
    sofr = _val(macro, "sofr")
    sofr_avg30 = _val(macro, "sofr_30day_avg")
    effr = _val(macro, "effr")
    iorb = _val(macro, "iorb")
    rrp = _val(macro, "reverse_repo")
    target_upper = _val(macro, "fed_target_upper")
    target_lower = _val(macro, "fed_target_lower")

    sofr_iorb_bp = round((sofr - iorb) * 100) if (sofr is not None and iorb is not None) else None
    sofr_effr_bp = round((sofr - effr) * 100) if (sofr is not None and effr is not None) else None

    rows = []
    for label, value, dec, suffix, note in [
        ("SOFR", sofr, 2, "%", "Secured Overnight Financing Rate: the benchmark for USD floating-rate debt, secured by Treasuries."),
        ("SOFR 30-day avg", sofr_avg30, 2, "%", "Smooths daily noise; the rate most loan documents actually reference."),
        ("EFFR", effr, 2, "%", "Effective Fed Funds Rate: the volume-weighted unsecured overnight rate the Fed targets."),
        ("IORB", iorb, 2, "%", "Interest on Reserve Balances: the Fed's administered floor for the funding market."),
        ("Reverse repo", rrp, 0, "B", "Cash parked overnight at the Fed. A drain toward zero means reserves are getting scarce."),
    ]:
        if value is None:
            continue
        rows.append({
            "label": label,
            "value": value,
            "display": f"${value:,.0f}B" if suffix == "B" else f"{value:.{dec}f}%",
            "change": _delta(macro, {
                "SOFR": "sofr", "SOFR 30-day avg": "sofr_30day_avg",
                "EFFR": "effr", "IORB": "iorb", "Reverse repo": "reverse_repo",
            }[label]),
            "note": note,
            "as_of": _macro(macro, {
                "SOFR": "sofr", "SOFR 30-day avg": "sofr_30day_avg",
                "EFFR": "effr", "IORB": "iorb", "Reverse repo": "reverse_repo",
            }[label]).get("date", ""),
        })

    return {
        "rows": rows,
        "sofr": sofr,
        "effr": effr,
        "iorb": iorb,
        "rrp": rrp,
        "sofr_iorb_bp": sofr_iorb_bp,
        "sofr_effr_bp": sofr_effr_bp,
        "target_range": (
            f"{target_lower:.2f}% to {target_upper:.2f}%"
            if (target_lower is not None and target_upper is not None) else None
        ),
        "read": _funding_read(sofr, iorb, sofr_iorb_bp, rrp, target_lower, target_upper),
    }


def _funding_read(
    sofr: float | None,
    iorb: float | None,
    sofr_iorb_bp: int | None,
    rrp: float | None,
    lower: float | None,
    upper: float | None,
) -> str:
    parts: list[str] = []

    if lower is not None and upper is not None:
        parts.append(
            f"The FOMC's target range is {lower:.2f}% to {upper:.2f}%."
        )

    if sofr_iorb_bp is None:
        parts.append("Overnight funding spreads are unavailable for this session.")
        return " ".join(parts)

    parts.append(
        f"SOFR is printing at {sofr:.2f}%, {sofr_iorb_bp:+d}bp versus the "
        f"{iorb:.2f}% interest-on-reserves floor."
    )

    # SOFR persistently above IORB means dealers are paying up for cash, which
    # is the cleanest early warning of reserve scarcity.
    if sofr_iorb_bp > 15:
        parts.append(
            "That is a wide premium. Secured funding trading well above the "
            "Fed's floor means collateral supply is heavy relative to available "
            "cash, the same dynamic that forced the Fed to intervene in "
            "September 2019. Watch it as the leading indicator of reserve scarcity."
        )
    elif sofr_iorb_bp > 5:
        parts.append(
            "Secured funding is printing above the Fed's floor, a mild sign "
            "that reserves are no longer abundant. Not stress, but worth tracking."
        )
    elif sofr_iorb_bp < -5:
        parts.append(
            "Secured funding below the floor indicates cash remains plentiful "
            "relative to collateral, the signature of an ample-reserve regime."
        )
    else:
        parts.append(
            "Funding is trading essentially on top of the Fed's floor, which is "
            "what a well-controlled money market looks like."
        )

    if rrp is not None:
        if rrp < 50:
            parts.append(
                f"The reverse repo facility holds just ${rrp:,.0f}B. With that "
                "buffer effectively drained, further balance sheet runoff comes "
                "straight out of bank reserves."
            )
        elif rrp < 300:
            parts.append(
                f"Reverse repo balances stand at ${rrp:,.0f}B, materially below "
                "the multi-trillion peak, meaning the system's excess-cash "
                "cushion has largely been absorbed."
            )
        else:
            parts.append(
                f"Reverse repo balances of ${rrp:,.0f}B indicate a still-sizable "
                "cash cushion in the system."
            )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Inflation expectations and real yields
# ---------------------------------------------------------------------------

def build_inflation(macro: dict) -> dict:
    be5 = _val(macro, "breakeven_5y")
    be10 = _val(macro, "breakeven_10y")
    real10 = _val(macro, "real_10y")
    nom10 = _val(macro, "ust_10y")

    d_be10 = _delta(macro, "breakeven_10y")
    d_real10 = _delta(macro, "real_10y")
    d_nom10 = _delta(macro, "ust_10y")

    rows = []
    for label, key, note in [
        ("5Y breakeven", "breakeven_5y", "Average annual CPI inflation the market expects over five years."),
        ("10Y breakeven", "breakeven_10y", "The same over ten years. Nominal yield minus TIPS yield."),
        ("10Y real yield", "real_10y", "The 10-year TIPS yield: the growth-and-policy component, stripped of inflation."),
    ]:
        value = _val(macro, key)
        if value is None:
            continue
        rows.append({
            "label": label,
            "display": f"{value:.2f}%",
            "change_display": _bp(_delta(macro, key)),
            "tone": _tone(_delta(macro, key)),
            "note": note,
            "as_of": _macro(macro, key).get("date", ""),
        })

    return {
        "rows": rows,
        "breakeven_5y": be5,
        "breakeven_10y": be10,
        "real_10y": real10,
        "read": _inflation_read(be10, real10, nom10, d_be10, d_real10, d_nom10),
    }


def _inflation_read(
    be10: float | None,
    real10: float | None,
    nom10: float | None,
    d_be: float | None,
    d_real: float | None,
    d_nom: float | None,
) -> str:
    if be10 is None or real10 is None:
        return "Breakeven and real yield data unavailable for this session."

    parts = [
        f"The 10-year nominal yield decomposes into a {real10:.2f}% real yield "
        f"and a {be10:.2f}% inflation breakeven. The breakeven is what the "
        "market expects average CPI inflation to run over the next decade; the "
        "real yield is the growth and policy component."
    ]

    if be10 > 2.5:
        parts.append(
            "At above 2.5%, breakevens sit meaningfully over the Fed's 2% "
            "target, indicating the market does not fully believe inflation "
            "returns to target on the Fed's timeline."
        )
    elif be10 < 2.0:
        parts.append(
            "Breakevens below 2% mean the market is pricing inflation to "
            "undershoot the Fed's target, which normally accompanies growth concerns."
        )
    else:
        parts.append(
            "Breakevens are close to the Fed's 2% target, indicating credible "
            "anchoring of long-run inflation expectations."
        )

    # Which component drove today's nominal move is the analytically useful
    # part, but only when the components actually moved. TIPS-derived series
    # sometimes lag the nominal curve by a publication day, which would
    # otherwise produce a confident attribution from two zeroes.
    if (
        d_nom is not None and d_be is not None and d_real is not None
        and abs(d_nom) >= 0.02
        and (abs(d_be) + abs(d_real)) >= 0.02
    ):
        driver = "the real yield" if abs(d_real) > abs(d_be) else "the inflation breakeven"
        parts.append(
            f"Today's {_bp(d_nom)} move in the 10-year was driven mainly by "
            f"{driver} ({_bp(d_real)} real, {_bp(d_be)} breakeven). "
            + (
                "A real-yield-led move is a growth or policy signal, and it is "
                "the component that discounts equity valuations."
                if abs(d_real) > abs(d_be)
                else "A breakeven-led move is an inflation expectations signal, "
                "which matters more for the Fed's reaction function than for "
                "the equity discount rate."
            )
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Credit conditions
# ---------------------------------------------------------------------------

def build_credit(macro: dict) -> dict:
    hy = _macro(macro, "hy_oas")
    ig = _macro(macro, "ig_oas")

    rows = []
    for label, data, key, note in [
        ("High yield OAS", hy, "hy_oas",
         "Option-adjusted spread over Treasuries on the US high yield index. The single best real-time gauge of risk appetite in credit."),
        ("Investment grade OAS", ig, "ig_oas",
         "The same for investment grade. Widening here means the cost of capital is rising for even high-quality borrowers."),
    ]:
        value = data.get("value")
        if value is None:
            continue
        rows.append({
            "label": label,
            "display": f"{value * 100:,.0f}bp",
            "change_display": (
                f"{(value - data['prev_value']) * 100:+,.0f}bp"
                if isinstance(data.get("prev_value"), (int, float)) else "n/a"
            ),
            "tone": _tone(
                (value - data["prev_value"]) if isinstance(data.get("prev_value"), (int, float)) else None,
                invert=True,
            ),
            "percentile": data.get("percentile_1y"),
            "range_1y": (
                f"{data['min_1y'] * 100:,.0f} to {data['max_1y'] * 100:,.0f}bp"
                if isinstance(data.get("min_1y"), (int, float))
                and isinstance(data.get("max_1y"), (int, float)) else None
            ),
            "note": note,
            "as_of": data.get("date", ""),
        })

    return {
        "rows": rows,
        "hy_oas_bp": round(hy["value"] * 100) if isinstance(hy.get("value"), (int, float)) else None,
        "ig_oas_bp": round(ig["value"] * 100) if isinstance(ig.get("value"), (int, float)) else None,
        "read": _credit_read(hy, ig),
    }


def _credit_read(hy: dict, ig: dict) -> str:
    hv = hy.get("value")
    if not isinstance(hv, (int, float)):
        return "Credit spread data unavailable for this session."

    hy_bp = round(hv * 100)
    pct = hy.get("percentile_1y")
    parts = [
        f"High yield spreads are at {hy_bp}bp over Treasuries."
    ]

    if isinstance(pct, (int, float)):
        parts.append(
            f"That is the {pct:.0f}th percentile of the past year, "
            + (
                "meaning credit is priced for a benign outcome and offers thin "
                "compensation for default risk."
                if pct < 25 else
                "meaning credit is pricing meaningful stress relative to the "
                "past year."
                if pct > 75 else
                "roughly mid-range versus the past year."
            )
        )

    if hy_bp < 300:
        parts.append(
            "Sub-300bp is historically tight. Tight spreads keep the leveraged "
            "finance and LBO machine running, because sponsors can raise debt "
            "cheaply, but they leave little cushion if defaults pick up."
        )
    elif hy_bp > 500:
        parts.append(
            "Above 500bp, high yield is signaling genuine risk aversion. That "
            "level typically shuts the LBO financing window and pushes deal "
            "activity toward all-equity or private credit structures."
        )
    else:
        parts.append(
            "That is a mid-cycle level: financing is available but issuers are "
            "paying for it."
        )

    iv = ig.get("value")
    if isinstance(iv, (int, float)):
        parts.append(
            f"Investment grade sits at {round(iv * 100)}bp, and the gap between "
            "the two indices is the market's price for moving down the quality curve."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Policy path
# ---------------------------------------------------------------------------

def build_policy(macro: dict) -> dict:
    """
    Curve-implied policy path.

    Methodology, stated in the email so it can be caveated out loud:
    the 1-year Treasury yield approximates the market's expected AVERAGE
    overnight rate over the coming year. The gap between the current effective
    funds rate and that yield is therefore the average easing priced in. For a
    path that eases gradually, the cumulative cut by the end of the window is
    roughly twice that average. Both figures are reported. This is a derived
    estimate, not CME FedWatch, and it will differ modestly from futures-implied
    probabilities because it ignores term premium.
    """
    effr = _val(macro, "effr")
    y1 = _val(macro, "ust_1y")
    m3 = _val(macro, "ust_3m")
    upper = _val(macro, "fed_target_upper")
    lower = _val(macro, "fed_target_lower")

    avg_easing_12m_bp = round((effr - y1) * 100) if (effr is not None and y1 is not None) else None
    terminal_easing_12m_bp = avg_easing_12m_bp * 2 if avg_easing_12m_bp is not None else None
    near_term_bp = round((effr - m3) * 100) if (effr is not None and m3 is not None) else None

    cuts_12m = (
        round(terminal_easing_12m_bp / 25, 1) if terminal_easing_12m_bp is not None else None
    )

    today = datetime.now(_ET).date()
    upcoming = [d for d in FOMC_DECISION_DATES if d >= today]
    next_fomc = upcoming[0] if upcoming else None

    return {
        "target_range": (
            f"{lower:.2f}% to {upper:.2f}%"
            if (lower is not None and upper is not None) else None
        ),
        "target_upper": upper,
        "effr": effr,
        "next_fomc": next_fomc.strftime("%B %d, %Y") if next_fomc else None,
        "days_to_fomc": (next_fomc - today).days if next_fomc else None,
        "meetings_ahead": [d.strftime("%b %d") for d in upcoming[:4]],
        "avg_easing_12m_bp": avg_easing_12m_bp,
        "terminal_easing_12m_bp": terminal_easing_12m_bp,
        "near_term_easing_bp": near_term_bp,
        "cuts_priced_12m": cuts_12m,
        "read": _policy_read(
            effr, y1, m3, avg_easing_12m_bp, terminal_easing_12m_bp,
            cuts_12m, near_term_bp, next_fomc, today,
        ),
        "methodology": (
            "Easing priced is derived from the Treasury curve, not from fed "
            "funds futures. The 1-year yield approximates the expected average "
            "overnight rate over the next year; doubling the gap to the current "
            "effective rate approximates the cumulative cut under a gradual "
            "path. Term premium is ignored, so these figures will differ "
            "modestly from CME FedWatch probabilities."
        ),
    }


def _policy_read(
    effr: float | None,
    y1: float | None,
    m3: float | None,
    avg_bp: int | None,
    term_bp: int | None,
    cuts: float | None,
    near_bp: int | None,
    next_fomc: date | None,
    today: date,
) -> str:
    if effr is None or y1 is None:
        return "Policy rate expectations cannot be derived without current funding and 1-year Treasury data."

    parts = [
        f"The effective funds rate is {effr:.2f}% and the 1-year Treasury yields "
        f"{y1:.2f}%."
    ]

    if term_bp is not None and term_bp > 12:
        parts.append(
            f"That gap implies roughly {avg_bp}bp of average easing over the "
            f"next twelve months, consistent with about {term_bp}bp of "
            f"cumulative cuts, or roughly {cuts:.1f} quarter-point moves, under "
            "a gradual path."
        )
    elif term_bp is not None and term_bp < -12:
        parts.append(
            f"The 1-year yield sits above the current funding rate, implying the "
            f"market prices roughly {abs(term_bp)}bp of cumulative tightening "
            "rather than cuts."
        )
    else:
        parts.append(
            "The gap is small enough that the market is pricing policy to stay "
            "roughly where it is over the next year."
        )

    if near_bp is not None and m3 is not None:
        parts.append(
            f"The 3-month bill at {m3:.2f}% sits {near_bp:+d}bp from the "
            "effective funds rate, which is the near-term policy signal."
        )

    if next_fomc:
        days = (next_fomc - today).days
        parts.append(
            f"The next FOMC decision lands {next_fomc.strftime('%B %d')}, "
            f"{days} days out."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Equities, sectors, cross-asset
# ---------------------------------------------------------------------------

def build_equity(snapshot: dict) -> dict:
    indices = []
    for key, label in [
        ("sp500", "S&P 500"),
        ("nasdaq", "NASDAQ 100"),
        ("dow", "Dow Jones"),
        ("russell2000", "Russell 2000"),
        ("vix", "VIX"),
    ]:
        q = _quote(snapshot, "primary", key)
        if q.get("value") is None:
            continue
        indices.append({
            "label": q.get("label", label),
            "value": q["value"],
            "display": f"{q['value']:,.2f}",
            "change_pct": q.get("change_pct"),
            "change_display": _signed(q.get("change_pct"), 2, "%"),
            "tone": _tone(q.get("change_pct"), invert=(key == "vix")),
        })

    futures = []
    for key in ("es_future", "nq_future", "ym_future"):
        q = _quote(snapshot, "futures", key)
        if q.get("value") is None:
            continue
        futures.append({
            "label": q.get("label", key),
            "display": f"{q['value']:,.2f}",
            "change_display": _signed(q.get("change_pct"), 2, "%"),
            "tone": _tone(q.get("change_pct")),
        })

    sectors_raw = (snapshot.get("sectors") or {})
    sectors = sorted(
        [
            {
                "label": v.get("label", k),
                "change_pct": v.get("change_pct"),
                "change_display": _signed(v.get("change_pct"), 2, "%"),
                "tone": _tone(v.get("change_pct")),
            }
            for k, v in sectors_raw.items()
            if v.get("change_pct") is not None
        ],
        key=lambda x: x["change_pct"],
        reverse=True,
    )

    international = []
    for key, q in (snapshot.get("international") or {}).items():
        if q.get("value") is None:
            continue
        international.append({
            "label": q.get("label", key),
            "display": f"{q['value']:,.2f}",
            "change_display": _signed(q.get("change_pct"), 2, "%"),
            "tone": _tone(q.get("change_pct")),
        })

    sp = _quote(snapshot, "primary", "sp500")
    rut = _quote(snapshot, "primary", "russell2000")
    ndx = _quote(snapshot, "primary", "nasdaq")
    vix = _quote(snapshot, "primary", "vix")

    return {
        "indices": indices,
        "futures": futures,
        "sectors": sectors,
        "international": international,
        "leaders": sectors[:3],
        "laggards": sectors[-3:][::-1] if len(sectors) >= 3 else [],
        "read": _equity_read(sp, ndx, rut, vix, sectors),
    }


_DEFENSIVE = {"Utilities", "Cons. Staples", "Health Care", "Real Estate"}
_CYCLICAL = {"Technology", "Cons. Discret.", "Financials", "Industrials", "Energy", "Materials"}


def _equity_read(sp: dict, ndx: dict, rut: dict, vix: dict, sectors: list[dict]) -> str:
    if sp.get("value") is None:
        return "Equity index data unavailable for this session."

    parts = [
        f"The S&P 500 closed at {sp['value']:,.2f}, "
        f"{_signed(sp.get('change_pct'), 2, '%')} on the session."
    ]

    # Small-cap versus large-cap is the cleanest available breadth proxy.
    if rut.get("change_pct") is not None and sp.get("change_pct") is not None:
        gap = rut["change_pct"] - sp["change_pct"]
        if abs(gap) >= 0.4:
            if gap > 0:
                parts.append(
                    f"The Russell 2000 outperformed by {abs(gap):.2f} points, "
                    "which is a breadth-positive signal: small caps are more "
                    "domestically exposed and more leveraged, so they lead when "
                    "the market is confident about growth or easier financing."
                )
            else:
                parts.append(
                    f"The Russell 2000 lagged by {abs(gap):.2f} points, a "
                    "narrow-leadership signal. Small caps carry more floating "
                    "rate debt and thinner margins, so they underperform when "
                    "the market doubts growth or the path of rates."
                )

    if ndx.get("change_pct") is not None and sp.get("change_pct") is not None:
        gap = ndx["change_pct"] - sp["change_pct"]
        if abs(gap) >= 0.3:
            parts.append(
                f"The NASDAQ 100 {'outperformed' if gap > 0 else 'underperformed'} "
                f"by {abs(gap):.2f} points, a {'growth' if gap > 0 else 'value'}-led "
                "session driven largely by long-duration equity sensitivity to rates."
            )

    if sectors:
        top = sectors[0]
        bottom = sectors[-1]
        parts.append(
            f"{top['label']} led at {top['change_display']} while "
            f"{bottom['label']} lagged at {bottom['change_display']}."
        )
        # Defensive versus cyclical tilt as a risk-appetite read.
        defensive = [s for s in sectors if s["label"] in _DEFENSIVE]
        cyclical = [s for s in sectors if s["label"] in _CYCLICAL]
        if defensive and cyclical:
            d_avg = sum(s["change_pct"] for s in defensive) / len(defensive)
            c_avg = sum(s["change_pct"] for s in cyclical) / len(cyclical)
            tilt = c_avg - d_avg
            if abs(tilt) >= 0.25:
                parts.append(
                    f"Cyclicals {'outpaced' if tilt > 0 else 'trailed'} defensives "
                    f"by {abs(tilt):.2f} points on average, a "
                    f"{'risk-on' if tilt > 0 else 'risk-off'} rotation."
                )

    if vix.get("value") is not None:
        v = vix["value"]
        context = (
            "complacent" if v < 14 else
            "calm" if v < 18 else
            "unsettled" if v < 25 else
            "stressed"
        )
        parts.append(
            f"The VIX at {v:.2f} is {context} by historical standards. The level "
            "matters for capital markets: the IPO window generally needs a VIX "
            "below roughly 20 to stay open."
        )

    return " ".join(parts)


def build_cross_asset(snapshot: dict) -> dict:
    def rows(group: str) -> list[dict]:
        out = []
        for key, q in (snapshot.get(group) or {}).items():
            if q.get("value") is None:
                continue
            fmt = q.get("format", "price")
            value = q["value"]
            display = (
                f"{value:,.4f}" if fmt == "fx"
                else f"{value:,.2f}" if fmt in ("price", "index")
                else f"${value:,.0f}"
            )
            out.append({
                "label": q.get("label", key),
                "display": display,
                "change_display": _signed(q.get("change_pct"), 2, "%"),
                "tone": _tone(q.get("change_pct")),
                "change_pct": q.get("change_pct"),
            })
        return out

    fx = rows("fx")
    commodities = rows("commodities")
    crypto = rows("crypto")

    return {
        "fx": fx,
        "commodities": commodities,
        "crypto": crypto,
        "read": _cross_asset_read(snapshot, fx, commodities),
    }


def _cross_asset_read(snapshot: dict, fx: list, commodities: list) -> str:
    parts: list[str] = []

    dxy = _quote(snapshot, "fx", "dxy")
    if dxy.get("value") is not None:
        parts.append(
            f"The dollar index is at {dxy['value']:,.2f}, "
            f"{_signed(dxy.get('change_pct'), 2, '%')}. A stronger dollar "
            "mechanically pressures commodity prices, tightens conditions for "
            "dollar borrowers abroad, and trims the reported earnings of US "
            "multinationals."
        )

    wti = _quote(snapshot, "commodities", "wti_crude")
    gold = _quote(snapshot, "commodities", "gold")
    copper = _quote(snapshot, "commodities", "copper")

    if wti.get("value") is not None:
        parts.append(
            f"WTI crude at ${wti['value']:,.2f} "
            f"({_signed(wti.get('change_pct'), 2, '%')}) feeds directly into "
            "headline inflation and into transport and chemical input costs."
        )

    if gold.get("value") is not None and copper.get("value") is not None:
        g_chg = gold.get("change_pct")
        c_chg = copper.get("change_pct")
        if g_chg is not None and c_chg is not None:
            if g_chg > 0.3 and c_chg < -0.3:
                signal = (
                    "Gold rising while copper falls is a defensive combination: "
                    "safe-haven demand up, industrial demand expectations down."
                )
            elif g_chg < -0.3 and c_chg > 0.3:
                signal = (
                    "Copper rising while gold falls is a pro-growth combination, "
                    "consistent with rising real yields and firmer industrial demand."
                )
            else:
                signal = (
                    "Gold and copper are moving in the same direction, which "
                    "usually points to a liquidity or dollar driver rather than "
                    "a pure growth signal."
                )
            parts.append(
                f"Gold at ${gold['value']:,.2f} ({_signed(g_chg, 2, '%')}) and "
                f"copper at ${copper['value']:,.2f} ({_signed(c_chg, 2, '%')}). "
                + signal
            )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Economic data
# ---------------------------------------------------------------------------

_ECON_DISPLAY: list[tuple[str, str, str, int, str]] = [
    ("cpi_yoy_rate", "CPI (YoY)", "%", 1, "Headline consumer inflation versus the Fed's 2% goal."),
    ("core_cpi_yoy_rate", "Core CPI (YoY)", "%", 1, "Excludes food and energy: the better read on underlying trend."),
    ("core_pce_yoy_rate", "Core PCE (YoY)", "%", 1, "The Fed's preferred inflation measure and the one it targets."),
    ("unemployment", "Unemployment", "%", 1, "The other half of the dual mandate."),
    ("payrolls_change", "Nonfarm payrolls", "K", 0, "Monthly job creation. The single most market-moving release."),
    ("real_gdp_growth", "Real GDP (QoQ ann.)", "%", 1, "Headline growth rate of the economy."),
    ("retail_sales_yoy", "Retail sales (YoY)", "%", 1, "Nominal consumer spending growth."),
    ("industrial_prod_yoy", "Industrial production (YoY)", "%", 1, "Factory, mining and utility output."),
    ("mortgage_30y", "30Y mortgage", "%", 2, "The transmission channel from Fed policy to housing."),
]


def build_econ(macro: dict) -> dict:
    rows = []
    for key, label, suffix, dec, note in _ECON_DISPLAY:
        data = _macro(macro, key)
        value = data.get("value")
        if value is None:
            continue
        rows.append({
            "label": label,
            "display": f"{value:,.{dec}f}{suffix}",
            "prev_display": (
                f"{data['prev_value']:,.{dec}f}{suffix}"
                if isinstance(data.get("prev_value"), (int, float)) else None
            ),
            "change_display": _signed(
                (value - data["prev_value"])
                if isinstance(data.get("prev_value"), (int, float)) else None,
                dec, suffix,
            ),
            "tone": _tone(
                (value - data["prev_value"])
                if isinstance(data.get("prev_value"), (int, float)) else None
            ),
            "as_of": data.get("date", ""),
            "note": note,
        })
    return {"rows": rows}


# ---------------------------------------------------------------------------
# Know These Cold
# ---------------------------------------------------------------------------

def build_key_numbers(snapshot: dict, macro: dict, curve: dict, policy: dict, credit: dict) -> list[dict]:
    """The memorize-these block. Every value is pulled or derived, never written."""
    items: list[dict] = []

    def add(label: str, display: str | None, sub: str = "", tone: str = "flat") -> None:
        if display and display != "n/a":
            items.append({"label": label, "value": display, "sub": sub, "tone": tone})

    sp = _quote(snapshot, "primary", "sp500")
    ndx = _quote(snapshot, "primary", "nasdaq")
    dow = _quote(snapshot, "primary", "dow")
    vix = _quote(snapshot, "primary", "vix")

    if sp.get("value"):
        add("S&P 500", f"{sp['value']:,.0f}", _signed(sp.get("change_pct"), 2, "%"), _tone(sp.get("change_pct")))
    if ndx.get("value"):
        add("NASDAQ 100", f"{ndx['value']:,.0f}", _signed(ndx.get("change_pct"), 2, "%"), _tone(ndx.get("change_pct")))
    if dow.get("value"):
        add("Dow", f"{dow['value']:,.0f}", _signed(dow.get("change_pct"), 2, "%"), _tone(dow.get("change_pct")))
    if vix.get("value"):
        add("VIX", f"{vix['value']:.1f}", _signed(vix.get("change_pct"), 1, "%"), _tone(vix.get("change_pct"), invert=True))

    t10 = _val(macro, "ust_10y")
    t2 = _val(macro, "ust_2y")
    if t10 is not None:
        add("10Y Treasury", f"{t10:.2f}%", _bp(_delta(macro, "ust_10y")), _tone(_delta(macro, "ust_10y")))
    if t2 is not None:
        add("2Y Treasury", f"{t2:.2f}%", _bp(_delta(macro, "ust_2y")), _tone(_delta(macro, "ust_2y")))
    if curve.get("spread_2s10s_bp") is not None:
        add("2s10s spread", f"{curve['spread_2s10s_bp']}bp",
            "inverted" if curve.get("inverted_2s10s") else "positive slope")

    if policy.get("target_range"):
        add("Fed target range", policy["target_range"], "current policy")
    if policy.get("effr") is not None:
        add("EFFR", f"{policy['effr']:.2f}%", "effective funds rate")
    sofr = _val(macro, "sofr")
    if sofr is not None:
        add("SOFR", f"{sofr:.2f}%", "overnight secured")
    if policy.get("next_fomc"):
        add("Next FOMC", policy["next_fomc"], f"{policy.get('days_to_fomc')} days out")
    # Sign matters here: a negative easing figure means the curve is pricing
    # tightening, and calling that "-2.9 cuts" would be actively misleading.
    # Lead with the basis-point figure, not the implied count of moves. The
    # bp number rests only on the observed gap between the effective funds
    # rate and the 1-year yield; converting it to a number of quarter-point
    # moves adds a gradual-path assumption. Both are shown, weakest last.
    cuts = policy.get("cuts_priced_12m")
    bps = policy.get("terminal_easing_12m_bp")
    if cuts is not None and bps is not None:
        if cuts >= 0.15:
            add("Easing priced (12m)", f"{abs(bps)}bp",
                f"about {abs(cuts):.1f} cuts, curve-derived")
        elif cuts <= -0.15:
            add("Tightening priced (12m)", f"{abs(bps)}bp",
                f"about {abs(cuts):.1f} hikes, curve-derived")
        else:
            add("Policy path (12m)", "flat", "no change priced, curve-derived")

    for key, label in [("cpi_yoy_rate", "CPI YoY"), ("core_pce_yoy_rate", "Core PCE YoY"),
                       ("unemployment", "Unemployment")]:
        v = _val(macro, key)
        if v is not None:
            add(label, f"{v:.1f}%", _macro(macro, key).get("date", ""))

    if credit.get("hy_oas_bp") is not None:
        add("HY OAS", f"{credit['hy_oas_bp']}bp", "risk appetite gauge")

    for group, key, label, dec, prefix in [
        ("commodities", "wti_crude", "WTI crude", 2, "$"),
        ("commodities", "gold", "Gold", 0, "$"),
        ("fx", "dxy", "Dollar index", 2, ""),
    ]:
        q = _quote(snapshot, group, key)
        if q.get("value") is not None:
            add(label, f"{prefix}{q['value']:,.{dec}f}",
                _signed(q.get("change_pct"), 2, "%"), _tone(q.get("change_pct")))

    return items


# ---------------------------------------------------------------------------
# Fact sheet handed to the model
# ---------------------------------------------------------------------------

def build_fact_sheet(engine: dict) -> str:
    """
    Compact plain-text digest of every computed figure.

    Injected into every model call as the authoritative numeric context. The
    prompt instructs the model that it may cite anything here and must not
    introduce a figure that is not here or in the day's source articles.
    """
    lines: list[str] = ["=== VERIFIED MARKET FACTS (authoritative; do not contradict) ==="]

    eq = engine.get("equity", {})
    if eq.get("indices"):
        lines.append("\nEQUITY INDICES (prior session close):")
        for i in eq["indices"]:
            lines.append(f"  {i['label']}: {i['display']} ({i['change_display']})")
    if eq.get("futures"):
        lines.append("\nOVERNIGHT FUTURES:")
        for i in eq["futures"]:
            lines.append(f"  {i['label']}: {i['display']} ({i['change_display']})")
    if eq.get("sectors"):
        lines.append("\nS&P SECTORS, best to worst:")
        lines.append("  " + "; ".join(f"{s['label']} {s['change_display']}" for s in eq["sectors"]))
    if eq.get("international"):
        lines.append("\nINTERNATIONAL:")
        lines.append("  " + "; ".join(f"{s['label']} {s['change_display']}" for s in eq["international"]))
    if eq.get("read"):
        lines.append(f"\nEQUITY READ: {eq['read']}")

    curve = engine.get("curve", {})
    if curve.get("points"):
        lines.append("\nTREASURY CURVE:")
        lines.append("  " + "; ".join(
            f"{p['label']} {p['display']} ({p['change_display']})" for p in curve["points"]
        ))
        lines.append(
            f"  2s10s: {curve.get('spread_2s10s_bp')}bp | "
            f"3M10Y: {curve.get('spread_3m10y_bp')}bp | "
            f"5s30s: {curve.get('spread_5s30s_bp')}bp"
        )
        lines.append(f"  CURVE READ: {curve.get('read')}")

    funding = engine.get("funding", {})
    if funding.get("rows"):
        lines.append("\nFUNDING MARKETS:")
        for r in funding["rows"]:
            lines.append(f"  {r['label']}: {r['display']}")
        lines.append(f"  SOFR less IORB: {funding.get('sofr_iorb_bp')}bp")
        lines.append(f"  FUNDING READ: {funding.get('read')}")

    infl = engine.get("inflation", {})
    if infl.get("rows"):
        lines.append("\nINFLATION EXPECTATIONS:")
        for r in infl["rows"]:
            lines.append(f"  {r['label']}: {r['display']} ({r['change_display']})")
        lines.append(f"  INFLATION READ: {infl.get('read')}")

    credit = engine.get("credit", {})
    if credit.get("rows"):
        lines.append("\nCREDIT SPREADS:")
        for r in credit["rows"]:
            extra = f", 1y range {r['range_1y']}" if r.get("range_1y") else ""
            lines.append(f"  {r['label']}: {r['display']} ({r['change_display']}){extra}")
        lines.append(f"  CREDIT READ: {credit.get('read')}")

    policy = engine.get("policy", {})
    if policy.get("read"):
        lines.append("\nPOLICY PATH:")
        lines.append(f"  Target range: {policy.get('target_range')}")
        lines.append(f"  Next FOMC: {policy.get('next_fomc')} ({policy.get('days_to_fomc')} days)")
        lines.append(
            f"  Curve-implied cumulative easing over 12m: "
            f"{policy.get('terminal_easing_12m_bp')}bp "
            f"({policy.get('cuts_priced_12m')} quarter-point cuts)"
        )
        lines.append(f"  POLICY READ: {policy.get('read')}")
        lines.append(f"  METHODOLOGY: {policy.get('methodology')}")

    ca = engine.get("cross_asset", {})
    if ca.get("commodities") or ca.get("fx"):
        lines.append("\nCROSS-ASSET:")
        for bucket in ("fx", "commodities", "crypto"):
            if ca.get(bucket):
                lines.append("  " + "; ".join(
                    f"{r['label']} {r['display']} ({r['change_display']})" for r in ca[bucket]
                ))
        if ca.get("read"):
            lines.append(f"  CROSS-ASSET READ: {ca['read']}")

    econ = engine.get("econ", {})
    if econ.get("rows"):
        lines.append("\nMACRO DATA (latest prints):")
        for r in econ["rows"]:
            prev = f", prior {r['prev_display']}" if r.get("prev_display") else ""
            lines.append(f"  {r['label']}: {r['display']}{prev} (as of {r['as_of']})")

    lines.append("\n=== END VERIFIED FACTS ===")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Compute the full deterministic layer from aggregated source data."""
    snapshot = raw_data.get("market_snapshot") or {}
    macro = raw_data.get("macro_data") or {}

    curve = build_curve(macro)
    funding = build_funding(macro)
    inflation = build_inflation(macro)
    credit = build_credit(macro)
    policy = build_policy(macro)
    equity = build_equity(snapshot)
    cross_asset = build_cross_asset(snapshot)
    econ = build_econ(macro)

    engine = {
        "curve": curve,
        "funding": funding,
        "inflation": inflation,
        "credit": credit,
        "policy": policy,
        "equity": equity,
        "cross_asset": cross_asset,
        "econ": econ,
        "key_numbers": build_key_numbers(snapshot, macro, curve, policy, credit),
    }
    engine["fact_sheet"] = build_fact_sheet(engine)

    logger.info(
        "Market engine built: %d key numbers, %d curve points, %d sectors.",
        len(engine["key_numbers"]),
        len(curve.get("points", [])),
        len(equity.get("sectors", [])),
    )
    return engine
