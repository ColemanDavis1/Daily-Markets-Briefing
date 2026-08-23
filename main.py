"""
Pipeline orchestrator.

  1. Aggregate news and market data
  2. Compute the deterministic market engine (every number in the issue)
  3. Synthesize the written sections through Claude
  4. Render the newsletter
  5. Deliver and log

Modes:
  python main.py                  full pipeline
  python main.py --dry-run        steps 1-4, writes briefing_preview.html, no send
  python main.py --prepare-only   steps 1-4, writes briefing_ready.html for a later send
  python main.py --send-only      sends the previously prepared briefing_ready.html
  python main.py --data-only      deterministic edition, zero model calls
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
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("briefing.main")
cfg = get_config()

_ROOT = Path(__file__).resolve().parent
_ET = ZoneInfo(cfg.timezone)
_READY_FILE = _ROOT / "briefing_ready.html"
_SUBJECT_FILE = _ROOT / "briefing_subject.txt"
_PREVIEW_FILE = _ROOT / "briefing_preview.html"


def _should_run_today() -> bool:
    if not cfg.weekdays_only:
        return True
    return datetime.now(_ET).weekday() < 5


def _skipped(mode: str) -> dict:
    logger.info("Weekend. Skipping %s (WEEKDAYS_ONLY is on).", mode)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "skipped_weekend",
        "mode": mode,
    }


def run_pipeline(
    *,
    dry_run: bool = False,
    prepare_only: bool = False,
    send_only: bool = False,
    data_only: bool = False,
) -> dict:
    if send_only:
        return _send_saved()

    if not dry_run and not _should_run_today():
        return _skipped("prepare" if prepare_only else "pipeline")

    run_log: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "prepare_only": prepare_only,
        "status": "started",
        "edition": None,
        "sections_generated": [],
        "degraded_sections": [],
        "corrections": 0,
        "sources_used": [],
        "sources_failed": [],
        "delivery": None,
        "error": None,
    }

    if dry_run:
        # A preview should always render, even with a half-configured .env, so
        # missing data keys warn rather than block.
        missing = []
        for key in cfg.validate_for_prepare():
            logger.warning(
                "%s is not set. The sections that depend on it will be empty.", key
            )
    else:
        missing = (
            cfg.validate_for_prepare() if prepare_only
            else cfg.validate_for_briefing()
        )
    if missing:
        run_log["status"] = "config_error"
        run_log["error"] = f"Missing config: {', '.join(missing)}"
        _append_log(run_log)
        raise RuntimeError(run_log["error"])

    try:
        # ---- 1. Aggregate ----
        logger.info("Step 1/5  Aggregating sources...")
        from news_aggregator import NewsAggregator
        raw_data = NewsAggregator().collect_all()
        run_log["sources_used"] = raw_data.get("sources_used", [])
        run_log["sources_failed"] = raw_data.get("sources_failed", [])

        # ---- 2. Deterministic engine ----
        logger.info("Step 2/5  Computing the market engine...")
        import market_engine
        engine = market_engine.build(raw_data)

        # ---- 3. Synthesize ----
        if data_only or not cfg.uses_model():
            logger.info("Step 3/5  Data edition requested. Skipping model calls.")
            from ai_synthesizer import compile_data_edition
            briefing = compile_data_edition(raw_data, engine, reason="data mode requested")
        else:
            logger.info("Step 3/5  Writing sections...")
            from ai_synthesizer import Synthesizer
            briefing = Synthesizer().synthesize(raw_data, engine)

        meta = briefing.get("_meta", {})
        run_log["edition"] = meta.get("edition")
        run_log["degraded_sections"] = meta.get("degraded_sections", [])
        run_log["corrections"] = sum(
            len(c.get("items", [])) for c in meta.get("corrections", [])
        )
        run_log["sections_generated"] = [k for k in briefing if k != "_meta"]
        logger.info(
            "  %d sections, %d degraded, %d corrections applied.",
            len(run_log["sections_generated"]),
            len(run_log["degraded_sections"]),
            run_log["corrections"],
        )

        # ---- 4. Render ----
        logger.info("Step 4/5  Rendering...")
        from email_renderer import EmailRenderer
        html, subject = EmailRenderer().render(
            briefing=briefing, engine=engine, raw_data=raw_data
        )
        run_log["subject"] = subject

        if dry_run:
            _PREVIEW_FILE.write_text(html, encoding="utf-8")
            run_log["status"] = "dry_run_complete"
            logger.info("Dry run complete. Preview at %s", _PREVIEW_FILE.name)
            logger.info("Subject would be: %s", subject)
            _append_log(run_log)
            return run_log

        if prepare_only:
            _READY_FILE.write_text(html, encoding="utf-8")
            _SUBJECT_FILE.write_text(subject, encoding="utf-8")
            run_log["status"] = "prepared"
            logger.info("Prepared. Saved %s", _READY_FILE.name)
            _append_log(run_log)
            return run_log

        # ---- 5. Send ----
        logger.info("Step 5/5  Sending...")
        run_log.update(_do_send(html, subject))

    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        _append_log(run_log)
        raise

    _append_log(run_log)
    return run_log


def _send_saved() -> dict:
    if not _should_run_today():
        return _skipped("send")

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
                f"{_READY_FILE.name} not found. Run --prepare-only first."
            )
        html = _READY_FILE.read_text(encoding="utf-8")
        subject = (
            _SUBJECT_FILE.read_text(encoding="utf-8").strip()
            if _SUBJECT_FILE.exists()
            else f"Morning Desk, {datetime.now(_ET).strftime('%b %d')}"
        )
        run_log.update(_do_send(html, subject))
    except Exception as exc:
        logger.error("Send failed: %s", exc, exc_info=True)
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        _append_log(run_log)
        raise
    _append_log(run_log)
    return run_log


def _do_send(html: str, subject: str) -> dict:
    from email_sender import EmailSender
    delivery = EmailSender().send(html_content=html, subject=subject)
    if not delivery.get("success"):
        raise RuntimeError(delivery.get("error", "email delivery failed"))
    logger.info("Delivered: %s", delivery)
    return {"status": "success", "delivery": delivery}


def _append_log(entry: dict) -> None:
    log_path = cfg.log_path
    logs: list = []
    if log_path.exists():
        try:
            loaded = json.loads(log_path.read_text(encoding="utf-8"))
            logs = loaded if isinstance(loaded, list) else []
        except (json.JSONDecodeError, OSError):
            logs = []
    logs.append(entry)
    log_path.write_text(json.dumps(logs[-180:], indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the morning briefing pipeline.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render to briefing_preview.html without sending.")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Render to briefing_ready.html for a later --send-only.")
    parser.add_argument("--send-only", action="store_true",
                        help="Send the previously prepared briefing_ready.html.")
    parser.add_argument("--data-only", action="store_true",
                        help="Deterministic edition with zero model calls.")
    args = parser.parse_args()

    try:
        result = run_pipeline(
            dry_run=args.dry_run,
            prepare_only=args.prepare_only,
            send_only=args.send_only,
            data_only=args.data_only,
        )
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)
    except Exception as e:
        print(f"Pipeline error: {e}", file=sys.stderr)
        sys.exit(1)
