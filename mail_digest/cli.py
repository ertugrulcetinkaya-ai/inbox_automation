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
from .sources.apple_mail import fetch_mail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but do not send")
    parser.add_argument(
        "--upcoming",
        action="store_true",
        help="List meetings scheduled today or later",
    )
    args = parser.parse_args()

    log("START daily meeting digest")
    log("Fetching recent meeting candidates")
    records = fetch_mail()
    if records is None:
        log("Failed to retrieve data.")
        log("DONE daily meeting digest (FAILED)")
        return 1

    log(f"Processed {len(records)} messages for {TARGET_EMAIL}")
    today = datetime.now().date()
    if args.upcoming:
        message = format_upcoming_digest(records, today)
        meeting_count = len(_collect_meetings(records, today))
        log(f"Found {meeting_count} meetings from {today.isoformat()} onward")
    else:
        message = format_digest(records, today)
        meeting_count = len(_collect_meetings(records, today, today))
        log(f"Found {meeting_count} meetings for {today.isoformat()}")

    if args.dry_run:
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
