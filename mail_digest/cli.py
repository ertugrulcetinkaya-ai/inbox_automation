"""Command-line entry point for the daily digest."""

import argparse
from datetime import datetime

from .config import TARGET_EMAIL, log
from .delivery.telegram import send_telegram
from .services.meeting_service import (
    _collect_meetings,
    format_digest,
    format_upcoming_digest,
)
from .services.lock import DigestAlreadyRunning, digest_lock
from .sources.apple_mail import fetch_mail


def _run_digest(upcoming=False, dry_run=False):
    log("START daily meeting digest")
    log("Fetching recent meeting candidates")
    records = fetch_mail()
    if records is None:
        log("Failed to retrieve data.")
        log("DONE daily meeting digest (FAILED)")
        return 1

    log(f"Processed {len(records)} messages for {TARGET_EMAIL}")
    today = datetime.now().date()
    if upcoming:
        message = format_upcoming_digest(records, today)
        meeting_count = len(_collect_meetings(records, today))
        log(f"Found {meeting_count} meetings from {today.isoformat()} onward")
    else:
        message = format_digest(records, today)
        meeting_count = len(_collect_meetings(records, today, today))
        log(f"Found {meeting_count} meetings for {today.isoformat()}")

    if dry_run:
        log("Dry-run mode: printing digest to stdout")
        print("\n--- DRY RUN DIGEST START ---\n")
        print(message)
        print("\n--- DRY RUN DIGEST END ---")
    else:
        log("Sending digest to Telegram")
        if send_telegram(message):
            log("Telegram send success")
        else:
            log("Telegram send failure")
            log("DONE daily meeting digest (FAILED)")
            return 1

    log("DONE daily meeting digest")
    return 0


def run_digest(upcoming=False, dry_run=False):
    """Run one digest while holding the shared process-level lock."""

    with digest_lock():
        return _run_digest(upcoming=upcoming, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but do not send")
    parser.add_argument(
        "--upcoming",
        action="store_true",
        help="List meetings scheduled today or later",
    )
    args = parser.parse_args()

    try:
        return run_digest(upcoming=args.upcoming, dry_run=args.dry_run)
    except DigestAlreadyRunning:
        # A concurrent launch is a normal no-op for launchd. The Telegram
        # listener calls run_digest() directly and handles this exception so
        # it can show the user a useful status message.
        log("Digest is already running; skipping this invocation")
        return 0
