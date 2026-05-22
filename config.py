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

    google_api_key: str = field(
        default_factory=lambda: os.getenv("GOOGLE_API_KEY", "")
    )
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
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

    def validate_for_briefing(self) -> List[str]:
        """Return list of missing required settings for a full run."""
        errors: List[str] = []
        if not self.google_api_key:
            errors.append("GOOGLE_API_KEY")
        if not self.recipient_emails:
            errors.append("RECIPIENT_EMAILS")
        if not self.sender_email:
            errors.append("SENDER_EMAIL")
        if not self.sendgrid_api_key and not (
            self.smtp_host and self.smtp_user and self.smtp_password
        ):
            errors.append("SENDGRID_API_KEY or SMTP credentials")
        return errors


def get_config() -> Config:
    return Config()
