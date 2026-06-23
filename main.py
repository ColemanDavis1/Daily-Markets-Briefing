"""
Pipeline orchestrator.

Runs the full briefing pipeline end-to-end:
  1. Aggregate news and market data
  2. Synthesize with Gemini
  3. Render HTML email
  4. Deliver via SendGrid / SMTP
  5. Log the result

Modes:
  python main.py                  Full pipeline (aggregate → synthesize → render → send)
  python main.py --prepare-only   Steps 1-3 only; saves rendered HTML to briefing_ready.html
  python main.py --send-only      Reads briefing_ready.html and sends (no Gemini calls)
  python main.py --dry-run        Steps 1-3, saves preview, no send
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("briefing.main")
cfg = get_config()

_ET = ZoneInfo("America/New_York")
_READY_FILE = Path(__file__).resolve().parent / "briefing_ready.html"


def run_pipeline(
    dry_run: bool = False,
    prepare_only: bool = False,
    send_only: bool = False,
) -> dict:
    """Execute the pipeline in the requested mode. Returns a run-log dict."""

    if send_only:
        return _send_saved()

    run_log: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "prepare_only": prepare_only,
        "status": "started",
        "sections_generated": [],
        "sources_used": [],
        "sources_failed": [],
        "delivery": None,
        "error": None,
    }

    missing = _missing_for_mode(dry_run=dry_run, prepare_only=prepare_only)
    if missing:
        run_log["status"] = "config_error"
        run_log["error"] = f"Missing config: {', '.join(missing)}"
        _append_log(run_log)
        raise RuntimeError(run_log["error"])

    try:
        # ---- Step 1: Aggregate ----
        logger.info("Step 1/4 — Aggregating news and market data...")
        from news_aggregator import NewsAggregator
        raw_data = NewsAggregator().collect_all()
        run_log["sources_used"] = raw_data.get("sources_used", [])
        run_log["sources_failed"] = raw_data.get("sources_failed", [])
        logger.info(
            "  %d sources used, %d failed.",
            len(run_log["sources_used"]),
            len(run_log["sources_failed"]),
        )

        # ---- Step 2: Synthesize ----
        logger.info("Step 2/4 — Synthesizing with Claude (%s)...", cfg.claude_model)
        from ai_synthesizer import AISynthesizer
        briefing = AISynthesizer().synthesize(raw_data)
        run_log["sections_generated"] = [
            k for k in briefing if k not in ("sources_used", "generation_notes")
        ]
        logger.info("  Sections generated: %s", ", ".join(run_log["sections_generated"]))

        # ---- Step 3: Render ----
        logger.info("Step 3/4 — Rendering HTML email template...")
        from email_renderer import EmailRenderer
        html = EmailRenderer().render(
            market_snapshot=raw_data.get("market_snapshot", {}),
            briefing=briefing,
        )

        if dry_run:
            Path(__file__).resolve().parent.joinpath("briefing_preview.html").write_text(
                html, encoding="utf-8"
            )
            run_log["status"] = "dry_run_complete"
            logger.info("Dry run complete. Preview saved to briefing_preview.html.")
            return run_log

        if prepare_only:
            _READY_FILE.write_text(html, encoding="utf-8")
            run_log["status"] = "prepared"
            logger.info("Prepare complete. Briefing saved to briefing_ready.html.")
            _append_log(run_log)
            return run_log

        # ---- Step 4: Send ----
        run_log.update(_do_send(html))

    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        _append_log(run_log)
        raise

    _append_log(run_log)
    return run_log


def _missing_for_mode(*, dry_run: bool, prepare_only: bool) -> list[str]:
    if dry_run or prepare_only:
        return cfg.validate_for_prepare()
    return cfg.validate_for_briefing()


def _send_saved() -> dict:
    """Read the prepared HTML and send it. Used by --send-only."""
    run_log: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "send_only": True,
        "status": "started",
        "delivery": None,
        "error": None,
    }
    try:
        missing = cfg.validate_for_send()
        if missing:
            raise RuntimeError(f"Missing config: {', '.join(missing)}")
        if not _READY_FILE.exists():
            raise FileNotFoundError(
                "briefing_ready.html not found — run --prepare-only first."
            )
        html = _READY_FILE.read_text(encoding="utf-8")
        run_log.update(_do_send(html))
    except Exception as exc:
        logger.error("Send failed: %s", exc, exc_info=True)
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        _append_log(run_log)
        raise
    _append_log(run_log)
    return run_log


def _do_send(html: str) -> dict:
    subject = f"Morning Briefing — {datetime.now(_ET).strftime('%A, %B %d, %Y')}"
    logger.info("Step 4/4 — Sending email: '%s'...", subject)
    from email_sender import EmailSender
    delivery = EmailSender().send(html_content=html, subject=subject)
    if not delivery.get("success"):
        raise RuntimeError(delivery.get("error", "Email delivery failed"))
    logger.info("  Delivery result: %s", delivery)
    return {"status": "success", "delivery": delivery}


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
    if len(logs) > 180:
        logs = logs[-180:]
    log_path.write_text(json.dumps(logs, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the morning briefing pipeline.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render but do not send. Saves briefing_preview.html.")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Aggregate, synthesize, and render. Save to briefing_ready.html.")
    parser.add_argument("--send-only", action="store_true",
                        help="Send the previously prepared briefing_ready.html.")
    args = parser.parse_args()

    try:
        result = run_pipeline(
            dry_run=args.dry_run,
            prepare_only=args.prepare_only,
            send_only=args.send_only,
        )
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)
    except Exception as e:
        print(f"Pipeline error: {e}", file=sys.stderr)
        sys.exit(1)
