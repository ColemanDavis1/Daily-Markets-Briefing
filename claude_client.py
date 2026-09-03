"""
Model execution backends.

Two interchangeable ways to reach Claude, behind one interface:

  cli   Shells out to the Claude Code CLI in headless mode. Billed against a
        Claude Pro or Max subscription rather than metered API credits, so the
        marginal cost of a run is zero. In CI, authenticate by setting the
        CLAUDE_CODE_OAUTH_TOKEN secret, generated locally with
        `claude setup-token`.

  api   The Anthropic SDK with an API key. Metered, used as the fallback.

Selected with LLM_BACKEND. The pipeline tries the configured backend, retries
once, then falls back to the other if it is available. If neither works the
caller degrades to a deterministic, numbers-only edition rather than shipping
nothing or shipping unverified prose.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time

from config import get_config

logger = logging.getLogger(__name__)
cfg = get_config()


class ModelUnavailable(RuntimeError):
    """No backend could service the request."""


class ModelCallFailed(RuntimeError):
    """A backend was reachable but the call did not produce usable output."""


class UsageLimitReached(ModelCallFailed):
    """
    The subscription's usage allowance is exhausted.

    Distinct from a normal failure because retrying is pointless and every
    further attempt wastes wall-clock time. The caller stops the run's
    remaining generation as soon as this appears.
    """


_LIMIT_MARKERS = (
    "hit your weekly limit",
    "hit your usage limit",
    "usage limit reached",
    "weekly limit",
    "rate limit",
    "too many requests",
    "quota",
)


def _is_limit_error(detail: str) -> bool:
    lowered = (detail or "").lower()
    return any(marker in lowered for marker in _LIMIT_MARKERS)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    """
    Pull the first JSON object out of a model response.

    Handles bare JSON, fenced blocks, and prose wrapped around an object,
    because no amount of prompting makes that fully deterministic.
    """
    if not text:
        raise ModelCallFailed("empty model response")

    cleaned = text.strip()

    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Brace matching, so a nested object does not truncate the parse.
    start = cleaned.find("{")
    if start == -1:
        raise ModelCallFailed(f"no JSON object in response: {cleaned[:200]}")

    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(cleaned[start:], start=start):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ModelCallFailed(f"malformed JSON: {exc}") from exc

    raise ModelCallFailed("unterminated JSON object in response")


# ---------------------------------------------------------------------------
# CLI backend
# ---------------------------------------------------------------------------

def _cli_available() -> bool:
    return shutil.which(cfg.claude_cli_path) is not None


def _call_cli(system_prompt: str, user_prompt: str, timeout: int) -> str:
    """
    Invoke the Claude Code CLI headlessly.

    The prompt goes in on stdin so long inputs are not subject to command line
    length limits. --max-turns 1 keeps this a single generation with no tool
    use, which is all a writing task needs.
    """
    executable = shutil.which(cfg.claude_cli_path)
    if not executable:
        raise ModelUnavailable(f"Claude CLI not found on PATH as {cfg.claude_cli_path!r}")

    command = [
        executable,
        "-p",
        "--output-format", "json",
        # No tools. This is a writing task, and with tools available the CLI
        # would reach for them, hit the turn limit mid-call, and return an
        # error carrying no text ("error_max_turns" with stop_reason
        # "tool_use"), which is what silently emptied whole packs.
        #
        # Disabling them also enforces the accuracy rule: the model cannot
        # search or read anything, so it can only use the fact sheet and the
        # source articles in the prompt.
        "--tools", "",
        # Headroom above the single turn a toolless generation needs, so a
        # stray internal step cannot truncate the response.
        "--max-turns", "3",
        "--append-system-prompt", system_prompt,
    ]
    if cfg.claude_cli_model:
        command += ["--model", cfg.claude_cli_model]

    env = os.environ.copy()
    # An OAuth token authenticates the subscription path in CI. Locally the
    # CLI uses the already-logged-in session and needs nothing here.
    if cfg.claude_oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = cfg.claude_oauth_token
    # Never let a stray API key silently divert a subscription run to metered billing.
    env.pop("ANTHROPIC_API_KEY", None)

    result = subprocess.run(
        command,
        input=user_prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    # The CLI exits non-zero on an API or auth error but still writes the
    # explanation to stdout as JSON, so parse before judging the exit code.
    envelope: dict | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
            envelope = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            envelope = None

    if envelope is not None and envelope.get("is_error"):
        # Pull whatever the envelope will give us. "no detail" responses are
        # useless for debugging, so fall back to the diagnostic fields.
        detail = str(envelope.get("result") or envelope.get("error") or "").strip()
        if not detail:
            diagnostics = {
                k: envelope.get(k)
                for k in ("subtype", "terminal_reason", "api_error_status",
                          "stop_reason", "num_turns")
                if envelope.get(k) not in (None, "")
            }
            detail = f"no message from CLI; diagnostics={diagnostics}"
        detail = detail[:400]

        if _is_limit_error(detail):
            raise UsageLimitReached(detail)
        if "authenticate" in detail.lower() or "oauth" in detail.lower():
            raise ModelCallFailed(
                f"{detail}. Re-authenticate the CLI with `claude login` locally, "
                "or refresh CLAUDE_CODE_OAUTH_TOKEN with `claude setup-token`."
            )
        raise ModelCallFailed(detail)

    if result.returncode != 0:
        raise ModelCallFailed(
            f"CLI exited {result.returncode}: {stderr or stdout[:300] or 'no output'}"
        )

    if not stdout:
        raise ModelCallFailed("CLI returned no output")

    if envelope is not None:
        # Notional token cost the CLI computes from usage. It is reported
        # regardless of whether the call was billed to a subscription or to
        # metered credits, so it is useful for sizing a run but says nothing
        # about which account paid. Billing path is determined by which
        # credential authenticated, not by this number.
        cost = envelope.get("total_cost_usd")
        if isinstance(cost, (int, float)) and cost > 0:
            logger.info("    Token usage for this call, notional: $%.4f", cost)

        for field in ("result", "text", "content", "response"):
            value = envelope.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return stdout


# ---------------------------------------------------------------------------
# API backend
# ---------------------------------------------------------------------------

def _api_available() -> bool:
    """
    The metered backend is opt-in only.

    LLM_ALLOW_API_FALLBACK must be explicitly true. Otherwise this returns
    False regardless of whether a key is present, so a run can never quietly
    move from subscription billing to paid API credits.
    """
    if not cfg.llm_allow_api_fallback:
        return False
    if not cfg.anthropic_api_key:
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _call_api(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    response = client.messages.create(
        model=cfg.claude_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def backend_status() -> dict[str, bool]:
    return {"cli": _cli_available(), "api": _api_available()}


def any_backend_available() -> bool:
    return any(backend_status().values())


def call_model(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int | None = None,
    timeout: int | None = None,
    label: str = "call",
) -> str:
    """
    Run one generation, preferring the configured backend.

    Order of attempts: preferred backend, one retry of it, then the other
    backend. Raises ModelUnavailable when nothing is reachable so the caller
    can fall back to a deterministic edition.
    """
    max_tokens = max_tokens or cfg.claude_max_tokens
    timeout = timeout or cfg.llm_timeout_sec

    preferred = cfg.llm_backend
    order = ["cli", "api"] if preferred == "cli" else ["api", "cli"]

    errors: list[str] = []

    for backend in order:
        available = _cli_available() if backend == "cli" else _api_available()
        if not available:
            errors.append(f"{backend}: not available")
            continue

        attempts = cfg.llm_retries if backend == preferred else 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                if backend == "cli":
                    text = _call_cli(system_prompt, user_prompt, timeout)
                else:
                    text = _call_api(system_prompt, user_prompt, max_tokens)
                logger.info(
                    "  %s served by %s in %.1fs (%d chars).",
                    label, backend, time.monotonic() - started, len(text),
                )
                return text
            except UsageLimitReached as exc:
                # Retrying cannot help and the allowance is shared across the
                # whole run, so surface this immediately.
                logger.error("  %s: usage limit reached (%s).", label, exc)
                raise
            except subprocess.TimeoutExpired:
                errors.append(f"{backend} attempt {attempt}: timeout after {timeout}s")
                logger.warning("  %s timed out on %s (attempt %d).", label, backend, attempt)
            except Exception as exc:
                errors.append(f"{backend} attempt {attempt}: {exc}")
                logger.warning("  %s failed on %s (attempt %d): %s", label, backend, attempt, exc)
            if attempt < attempts:
                time.sleep(cfg.llm_retry_delay_sec)

    raise ModelUnavailable(f"all backends failed for {label}. " + " | ".join(errors))


def call_model_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int | None = None,
    timeout: int | None = None,
    label: str = "call",
) -> dict:
    """call_model plus JSON parsing, with one reprompt if parsing fails."""
    text = call_model(
        system_prompt, user_prompt,
        max_tokens=max_tokens, timeout=timeout, label=label,
    )
    try:
        return extract_json(text)
    except ModelCallFailed as exc:
        logger.warning("  %s returned unparseable JSON (%s). Reprompting once.", label, exc)
        text = call_model(
            system_prompt,
            user_prompt + (
                "\n\nYour previous response could not be parsed. Return ONLY a "
                "single valid JSON object matching the schema. No prose, no "
                "markdown fences, no commentary."
            ),
            max_tokens=max_tokens, timeout=timeout, label=f"{label} (retry)",
        )
        return extract_json(text)
