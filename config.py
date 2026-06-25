"""Central configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


def _parse_recipients(raw: str | None) -> List[str]:
    if not raw:
        return []
    return [e.strip() for e in raw.split(",") if e.strip()]


@dataclass
class Config:
    """Application configuration."""

    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    )
    claude_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("CLAUDE_MAX_TOKENS", "2048"))
    )
    # "digest" = zero Claude calls, headlines + data feeds only
    # "light" = concise AI analysis (9 calls/run); "full" = deep analysis
    briefing_mode: str = field(
        default_factory=lambda: os.getenv("BRIEFING_MODE", "digest").lower()
    )
    # Skip Saturdays and Sundays (in TIMEZONE)
    weekdays_only: bool = field(
        default_factory=lambda: os.getenv("WEEKDAYS_ONLY", "true").lower()
        in ("1", "true", "yes")
    )
    verify_sections: bool = field(
        default_factory=lambda: os.getenv("VERIFY_SECTIONS", "false").lower()
        in ("1", "true", "yes")
    )
    verify_only_sections: List[str] = field(
        default_factory=lambda: _parse_recipients(os.getenv("VERIFY_ONLY_SECTIONS"))
    )
    section_delay_sec: float = field(
        default_factory=lambda: float(
            os.getenv("SECTION_DELAY_SEC", os.getenv("GEMINI_SECTION_DELAY_SEC", "1"))
        )
    )
    sendgrid_api_key: str = field(
        default_factory=lambda: os.getenv("SENDGRID_API_KEY", "")
    )
    sender_email: str = field(
        default_factory=lambda: os.getenv("SENDER_EMAIL", "")
    )
    recipient_emails: List[str] = field(
        default_factory=lambda: _parse_recipients(os.getenv("RECIPIENT_EMAILS"))
    )
    admin_email: str = field(
        default_factory=lambda: os.getenv("ADMIN_EMAIL", "")
    )
    timezone: str = field(
        default_factory=lambda: os.getenv("TIMEZONE", "America/New_York")
    )
    schedule_hour: int = field(
        default_factory=lambda: int(os.getenv("SCHEDULE_HOUR", "9"))
    )
    schedule_minute: int = field(
        default_factory=lambda: int(os.getenv("SCHEDULE_MINUTE", "30"))
    )

    # SMTP fallback
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(
        default_factory=lambda: int(os.getenv("SMTP_PORT", "587"))
    )
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(
        default_factory=lambda: os.getenv("SMTP_PASSWORD", "")
    )

    # News APIs (all optional — pipeline degrades gracefully without them)
    finnhub_api_key: str = field(
        default_factory=lambda: os.getenv("FINNHUB_API_KEY", "")
    )
    fred_api_key: str = field(
        default_factory=lambda: os.getenv("FRED_API_KEY", "")
    )
    news_api_key: str = field(
        default_factory=lambda: os.getenv("NEWS_API_KEY", "")
    )

    # Optional
    unsubscribe_url: str = field(
        default_factory=lambda: os.getenv(
            "UNSUBSCRIBE_URL", "mailto:unsubscribe@example.com"
        )
    )
    log_path: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "briefing_log.json"
    )
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)

    def validate_for_prepare(self) -> List[str]:
        """Settings required to aggregate, synthesize, and render."""
        errors: List[str] = []
        if self.briefing_mode != "digest" and not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY")
        return errors

    def uses_claude(self) -> bool:
        return self.briefing_mode not in ("digest", "data")

    def validate_for_send(self) -> List[str]:
        """Settings required to deliver email."""
        errors: List[str] = []
        if not self.recipient_emails:
            errors.append("RECIPIENT_EMAILS")
        if not self.sender_email:
            errors.append("SENDER_EMAIL")
        if not self.sendgrid_api_key and not (
            self.smtp_host and self.smtp_user and self.smtp_password
        ):
            errors.append("SENDGRID_API_KEY or SMTP credentials")
        return errors

    def validate_for_briefing(self) -> List[str]:
        """Return list of missing required settings for a full run."""
        return list(dict.fromkeys(
            self.validate_for_prepare() + self.validate_for_send()
        ))


def get_config() -> Config:
    return Config()
