"""Central configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


def _csv(raw: str | None) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Application configuration."""

    # ---------------------------------------------------------------
    # Model execution
    # ---------------------------------------------------------------
    # "cli" runs generation through the Claude Code CLI against a Pro or Max
    # subscription (zero marginal cost). "api" uses metered API credits.
    llm_backend: str = field(
        default_factory=lambda: os.getenv("LLM_BACKEND", "cli").strip().lower()
    )
    claude_cli_path: str = field(
        default_factory=lambda: os.getenv("CLAUDE_CLI_PATH", "claude")
    )
    # Model alias passed to the CLI. Empty means the CLI's own default.
    claude_cli_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_CLI_MODEL", "sonnet").strip()
    )
    # Generated locally with `claude setup-token`; required only in CI.
    claude_oauth_token: str = field(
        default_factory=lambda: os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip()
    )
    # Hard guard against surprise spend. While this is false the metered API
    # backend is never used, even if an ANTHROPIC_API_KEY happens to be in the
    # environment and even if the CLI is unreachable. A run with no working
    # subscription auth ships the deterministic edition instead of billing.
    llm_allow_api_fallback: bool = field(
        default_factory=lambda: _flag("LLM_ALLOW_API_FALLBACK", "false")
    )
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
    )
    claude_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("CLAUDE_MAX_TOKENS", "8192"))
    )
    llm_timeout_sec: int = field(
        default_factory=lambda: int(os.getenv("LLM_TIMEOUT_SEC", "300"))
    )
    llm_retries: int = field(
        default_factory=lambda: int(os.getenv("LLM_RETRIES", "2"))
    )
    llm_retry_delay_sec: float = field(
        default_factory=lambda: float(os.getenv("LLM_RETRY_DELAY_SEC", "4"))
    )
    section_delay_sec: float = field(
        default_factory=lambda: float(os.getenv("SECTION_DELAY_SEC", "0.5"))
    )

    # ---------------------------------------------------------------
    # Editorial
    # ---------------------------------------------------------------
    # "newsletter" is the full briefing. "data" ships the deterministic
    # numbers-only edition with no model calls at all.
    briefing_mode: str = field(
        default_factory=lambda: os.getenv("BRIEFING_MODE", "newsletter").strip().lower()
    )
    # Second model pass that checks every figure against the computed fact
    # sheet and strips unsupported claims.
    verify_sections: bool = field(default_factory=lambda: _flag("VERIFY_SECTIONS", "true"))
    # Words per group story. The lead sections get their own larger budgets.
    coverage_story_words: int = field(
        default_factory=lambda: int(os.getenv("COVERAGE_STORY_WORDS", "200"))
    )
    product_story_words: int = field(
        default_factory=lambda: int(os.getenv("PRODUCT_STORY_WORDS", "180"))
    )
    # Groups to skip entirely, comma separated (e.g. "restructuring,dcm").
    skip_groups: List[str] = field(
        default_factory=lambda: _csv(os.getenv("SKIP_GROUPS"))
    )

    # ---------------------------------------------------------------
    # Scheduling
    # ---------------------------------------------------------------
    weekdays_only: bool = field(default_factory=lambda: _flag("WEEKDAYS_ONLY", "true"))
    timezone: str = field(
        default_factory=lambda: os.getenv("TIMEZONE", "America/New_York")
    )
    schedule_hour: int = field(default_factory=lambda: int(os.getenv("SCHEDULE_HOUR", "7")))
    schedule_minute: int = field(default_factory=lambda: int(os.getenv("SCHEDULE_MINUTE", "0")))

    # ---------------------------------------------------------------
    # Delivery
    # ---------------------------------------------------------------
    sendgrid_api_key: str = field(
        default_factory=lambda: os.getenv("SENDGRID_API_KEY", "")
    )
    sender_email: str = field(default_factory=lambda: os.getenv("SENDER_EMAIL", ""))
    sender_name: str = field(
        default_factory=lambda: os.getenv("SENDER_NAME", "The Morning Desk")
    )
    recipient_emails: List[str] = field(
        default_factory=lambda: _csv(os.getenv("RECIPIENT_EMAILS"))
    )
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", ""))
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))

    # ---------------------------------------------------------------
    # Data sources
    # ---------------------------------------------------------------
    finnhub_api_key: str = field(default_factory=lambda: os.getenv("FINNHUB_API_KEY", ""))
    fred_api_key: str = field(default_factory=lambda: os.getenv("FRED_API_KEY", "").strip())
    news_api_key: str = field(default_factory=lambda: os.getenv("NEWS_API_KEY", ""))

    # ---------------------------------------------------------------
    # Misc
    # ---------------------------------------------------------------
    unsubscribe_url: str = field(
        default_factory=lambda: os.getenv("UNSUBSCRIBE_URL", "mailto:unsubscribe@example.com")
    )
    log_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "briefing_log.json")
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)

    # ---------------------------------------------------------------
    # Derived
    # ---------------------------------------------------------------

    def uses_model(self) -> bool:
        """False in data mode, where the briefing is fully deterministic."""
        return self.briefing_mode != "data"

    def validate_for_prepare(self) -> List[str]:
        """
        Settings required to aggregate and render.

        The model backend is deliberately not required: if it is missing the
        pipeline ships the deterministic edition rather than failing. FRED is
        listed as a warning-level gap because without it the entire rates and
        funding core of the briefing disappears.
        """
        missing: List[str] = []
        if not self.fred_api_key:
            missing.append("FRED_API_KEY")
        return missing

    def validate_for_send(self) -> List[str]:
        missing: List[str] = []
        if not self.recipient_emails:
            missing.append("RECIPIENT_EMAILS")
        if not self.sender_email:
            missing.append("SENDER_EMAIL")
        if not self.sendgrid_api_key and not (
            self.smtp_host and self.smtp_user and self.smtp_password
        ):
            missing.append("SENDGRID_API_KEY or SMTP credentials")
        return missing

    def validate_for_briefing(self) -> List[str]:
        return list(dict.fromkeys(self.validate_for_prepare() + self.validate_for_send()))


def get_config() -> Config:
    return Config()
