#!/usr/bin/env python3
"""Measure parser timings on synthetic messages without Mail or Telegram."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mail_digest.config import local_now  # noqa: E402
from mail_digest.services.meeting_service import (  # noqa: E402
    build_digest_result,
    due_reminder_meetings,
)
from mail_digest.services import meeting_service  # noqa: E402
from mail_digest.services.parse_cache import ParseCacheUnavailable  # noqa: E402


def synthetic_records(count: int, tokens_per_message: int, event_start):
    noise = " ".join(["12.03"] * tokens_per_message)
    prefix = f"Meeting scheduled {event_start.date().isoformat()} at {event_start:%H:%M}."
    padding = "x" * max(0, 20_000 - len(prefix) - len(noise) - 2)
    content = f"{prefix} {padding} {noise}"
    return [
        {
            "subject": f"Synthetic meeting {index:03d}",
            "sender": "Fixture Organizer <organizer@example.test>",
            "received_date": event_start.date().isoformat(),
            "source_message_id": f"synthetic-{index:03d}@example.test",
            "content": content,
        }
        for index in range(count)
    ]


def measure(label, action):
    started = time.perf_counter()
    result = action()
    elapsed = time.perf_counter() - started
    count = getattr(result, "count", None)
    if not isinstance(count, int):
        count = len(result)
    print(f"{label}: {elapsed:.3f}s ({count} meetings)")
    return elapsed


def benchmark_actions(records, target_date, now):
    return [
        (
            "daily",
            lambda: build_digest_result(records, "daily", target_date=target_date),
        ),
        (
            "weekly",
            lambda: build_digest_result(records, "weekly", target_date=target_date),
        ),
        (
            "upcoming",
            lambda: build_digest_result(records, "upcoming", target_date=target_date),
        ),
        ("due_reminder_meetings", lambda: due_reminder_meetings(records, now=now)),
    ]


def measure_uncached(actions):
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with patch.object(
            meeting_service,
            "ParseCache",
            side_effect=ParseCacheUnavailable,
        ):
            for name, action in actions:
                measure(f"uncached {name}", action)
    finally:
        logging.disable(previous_disable)


def measure_cached(actions, cache_dir):
    for name, action in actions:
        cache_file = Path(cache_dir) / f"{name}.sqlite3"
        with patch.dict(os.environ, {"MAIL_DIGEST_PARSE_CACHE_FILE": str(cache_file)}):
            measure(f"cold-cache {name}", action)
            measure(f"warm-cache {name}", action)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=300)
    parser.add_argument("--tokens-per-message", type=int, default=400)
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Compare uncached, cold-cache, and warm-cache timings using a temporary cache",
    )
    args = parser.parse_args()
    if args.messages <= 0 or args.tokens_per_message <= 0:
        parser.error("--messages and --tokens-per-message must be positive")

    now = local_now().replace(second=0, microsecond=0)
    event_start = now + timedelta(minutes=10)
    records = synthetic_records(args.messages, args.tokens_per_message, event_start)
    target_date = event_start.date()
    actions = benchmark_actions(records, target_date, now)
    measure_uncached(actions)
    if args.cache:
        with tempfile.TemporaryDirectory(prefix="inbox-automation-parse-cache-") as cache_dir:
            measure_cached(actions, cache_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
