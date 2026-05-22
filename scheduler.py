"""
Scheduler module.

Runs the briefing pipeline at the configured time (default: 9:30 AM, Mon–Fri).
Retries up to MAX_RETRIES times on failure with exponential backoff.
Keeps a lightweight health status in briefing_log.json on every attempt.

Run:  python scheduler.py
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("briefing.scheduler")

cfg = get_config()
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [60, 120, 300]  # wait before each retry attempt


def scheduled_job() -> None:
    """Entry point called by APScheduler. Wraps run_pipeline with retry logic."""
    logger.info(
        "Scheduled job triggered at %s (%s)",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        cfg.timezone,
    )

    from main import run_pipeline

    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Pipeline attempt %d/%d...", attempt, MAX_RETRIES)
            result = run_pipeline()
            logger.info("Pipeline completed successfully on attempt %d.", attempt)
            _log_health("success", attempt, result)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Attempt %d/%d failed: %s",
                attempt, MAX_RETRIES, exc,
            )
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_SECONDS[attempt - 1]
                logger.info("Retrying in %ds...", backoff)
                time.sleep(backoff)

    # All attempts exhausted
    logger.error(
        "All %d pipeline attempts failed. Last error: %s",
        MAX_RETRIES, last_exc,
    )
    _log_health("all_retries_failed", MAX_RETRIES, {"error": str(last_exc)})


def _log_health(status: str, attempts: int, detail: dict) -> None:
    """Append a compact health entry to the log (distinct from the full run log)."""
    from main import _append_log
    _append_log({
        "timestamp": datetime.utcnow().isoformat(),
        "event": "scheduler_health",
        "status": status,
        "attempts": attempts,
        "detail": detail,
    })


def start() -> None:
    tz = pytz.timezone(cfg.timezone)
    scheduler = BlockingScheduler(timezone=tz)

    trigger = CronTrigger(
        day_of_week="mon-fri",
        hour=cfg.schedule_hour,
        minute=cfg.schedule_minute,
        timezone=tz,
    )

    scheduler.add_job(
        scheduled_job,
        trigger=trigger,
        id="morning_briefing",
        name="Morning Briefing Pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    next_run = scheduler.get_jobs()[0].next_run_time
    logger.info(
        "Scheduler started. Next run: %s (%s, Mon–Fri %02d:%02d).",
        next_run.strftime("%Y-%m-%d %H:%M %Z") if next_run else "unknown",
        cfg.timezone,
        cfg.schedule_hour,
        cfg.schedule_minute,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    start()
