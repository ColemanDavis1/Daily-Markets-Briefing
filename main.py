"""
Pipeline orchestrator.

Runs the full briefing pipeline end-to-end:
  1. Aggregate news and market data
  2. Synthesize with Claude
  3. Render HTML email
  4. Deliver via SendGrid / SMTP
  5. Log the result

Run manually:  python main.py
Run in check mode (no email sent):  python main.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("briefing.main")
cfg = get_config()


def run_pipeline(dry_run: bool = False) -> dict:
    """
    Execute the full pipeline. Returns a run-log dict.

    Raises on unrecoverable error so the caller (scheduler) can handle retry.
    """
    run_log: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "status": "started",
        "sections_generated": [],
        "sources_used": [],
        "sources_failed": [],
        "delivery": None,
        "error": None,
    }

    # Validate config before doing any work
    missing = cfg.validate_for_briefing()
    if missing and not dry_run:
        run_log["status"] = "config_error"
        run_log["error"] = f"Missing config: {', '.join(missing)}"
        _append_log(run_log)
        raise RuntimeError(run_log["error"])

    try:
        # ---- Step 1: Aggregate ----
        logger.info("Step 1/4 — Aggregating news and market data...")
        from news_aggregator import NewsAggregator
        aggregator = NewsAggregator()
        raw_data = aggregator.collect_all()
        run_log["sources_used"] = raw_data.get("sources_used", [])
        run_log["sources_failed"] = raw_data.get("sources_failed", [])
        logger.info(
            "  %d headlines collected, %d sources used, %d failed.",
            len(raw_data.get("headlines", [])),
            len(run_log["sources_used"]),
            len(run_log["sources_failed"]),
        )

        # ---- Step 2: Synthesize ----
        logger.info("Step 2/4 — Synthesizing with Gemini (%s)...", cfg.gemini_model)
        from ai_synthesizer import AISynthesizer
        synthesizer = AISynthesizer()
        briefing = synthesizer.synthesize(raw_data)
        run_log["sections_generated"] = [
            k for k in briefing
            if k not in ("sources_used", "generation_notes")
        ]
        logger.info("  Sections generated: %s", ", ".join(run_log["sections_generated"]))

        # ---- Step 3: Render ----
        logger.info("Step 3/4 — Rendering HTML email template...")
        from email_renderer import EmailRenderer
        renderer = EmailRenderer()
        html = renderer.render(
            market_snapshot=raw_data.get("market_snapshot", {}),
            briefing=briefing,
        )

        if dry_run:
            _write_preview(html)
            run_log["status"] = "dry_run_complete"
            logger.info("Dry run complete. Preview saved to briefing_preview.html.")
            return run_log

        # ---- Step 4: Send ----
        from datetime import datetime as dt
        subject = f"Morning Briefing — {dt.now().strftime('%A, %B %d, %Y')}"
        logger.info("Step 4/4 — Sending email: '%s'...", subject)
        from email_sender import EmailSender
        sender = EmailSender()
        delivery = sender.send(html_content=html, subject=subject)
        run_log["delivery"] = delivery
        run_log["status"] = "success" if delivery.get("success") else "delivery_failed"
        logger.info("  Delivery result: %s", delivery)

    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        _append_log(run_log)
        raise

    _append_log(run_log)
    return run_log


# ---------------------------------------------------------------------------
# Log persistence
# ---------------------------------------------------------------------------

def _append_log(entry: dict) -> None:
    log_path = cfg.log_path
    logs: list = []

    if log_path.exists():
        try:
            logs = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(logs, list):
                logs = []
        except (json.JSONDecodeError, OSError):
            logs = []

    logs.append(entry)
    # Keep last 180 entries (~6 months of weekdays)
    if len(logs) > 180:
        logs = logs[-180:]

    log_path.write_text(json.dumps(logs, indent=2, default=str), encoding="utf-8")


def _write_preview(html: str) -> None:
    preview_path = Path(__file__).resolve().parent / "briefing_preview.html"
    preview_path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the morning briefing pipeline.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Aggregate and render but skip email delivery. Saves briefing_preview.html.",
    )
    args = parser.parse_args()

    try:
        result = run_pipeline(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)
    except Exception as e:
        print(f"Pipeline error: {e}", file=sys.stderr)
        sys.exit(1)
