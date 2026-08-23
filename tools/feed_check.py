"""
Feed health audit.

RSS endpoints rot quietly: a publisher retires a feed and it returns zero
entries, or it keeps responding while serving content frozen months ago. Both
failure modes are invisible in a normal run, so check them deliberately.

    python tools/feed_check.py

Flags any feed that is empty or whose newest item is more than three days old.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402

from news_aggregator import RSS_FEEDS, _BROWSER_UA, _parse_date  # noqa: E402

STALE_AFTER_DAYS = 3


def main() -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_AFTER_DAYS)
    problems: list[str] = []

    print(f"{'feed':22} {'items':>6}  {'newest':<26} status")
    print("-" * 72)

    for key, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": _BROWSER_UA})
        except Exception as exc:
            print(f"{key:22} {'':>6}  {'':<26} ERROR {exc}")
            problems.append(f"{key}: {exc}")
            continue

        count = len(feed.entries)
        if not count:
            print(f"{key:22} {0:>6}  {'-':<26} EMPTY")
            problems.append(f"{key}: returns no entries")
            continue

        raw = feed.entries[0].get("published", feed.entries[0].get("updated", ""))
        newest_str = _parse_date(raw)
        status = "ok"
        try:
            newest = datetime.fromisoformat(newest_str)
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            if newest < cutoff:
                status = f"STALE ({(now - newest).days}d old)"
                problems.append(f"{key}: newest item is {(now - newest).days} days old")
        except ValueError:
            status = "unparseable date"

        print(f"{key:22} {count:>6}  {newest_str[:26]:<26} {status}")

    print("-" * 72)
    if problems:
        print(f"\n{len(problems)} feed(s) need attention:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nAll feeds healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
