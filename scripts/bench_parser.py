#!/usr/bin/env python3
"""Measure parser timings on synthetic messages without Mail or Telegram."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mail_digest.config import local_now  # noqa: E402
from mail_digest.services.meeting_service import (  # noqa: E402
    build_digest_result,
    due_reminder_meetings,
)


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


def measure(label, action, count_of):
    started = time.perf_counter()
    result = action()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.3f}s ({count_of(result)} meetings)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=300)
    parser.add_argument("--tokens-per-message", type=int, default=400)
    args = parser.parse_args()
    if args.messages <= 0 or args.tokens_per_message <= 0:
        parser.error("--messages and --tokens-per-message must be positive")

    now = local_now().replace(second=0, microsecond=0)
    event_start = now + timedelta(minutes=10)
    records = synthetic_records(args.messages, args.tokens_per_message, event_start)
    target_date = event_start.date()

    measure(
        "daily",
        lambda: build_digest_result(records, "daily", target_date=target_date),
        lambda result: result.count,
    )
    measure(
        "weekly",
        lambda: build_digest_result(records, "weekly", target_date=target_date),
        lambda result: result.count,
    )
    measure(
        "upcoming",
        lambda: build_digest_result(records, "upcoming", target_date=target_date),
        lambda result: result.count,
    )
    measure(
        "due_reminder_meetings",
        lambda: due_reminder_meetings(records, now=now),
        len,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
