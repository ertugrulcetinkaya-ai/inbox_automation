"""Command-line entry point for the daily digest."""

import argparse
from datetime import timedelta

from .config import TARGET_EMAIL, local_now, log, reminder_minutes, reminder_window_minutes
from .delivery.telegram import send_telegram
from .services.meeting_service import (
    _collect_meetings,
    due_reminder_meetings,
    format_attention_digest,
    format_digest,
    format_reminder_digest,
    format_upcoming_digest,
    format_weekly_digest,
)
from .services.lock import DigestAlreadyRunning, digest_lock
from .services.reminder_state import (
    load_last_successful_reminder_scan,
    load_sent_reminders,
    mark_reminder_scan_succeeded,
    mark_reminders_sent,
    reminder_key,
)
from .sources import MailSourceConfigurationError, fetch_mail, selected_source_name


def _run_digest(upcoming=False, dry_run=False, mode=None):
    selected_mode = mode or ("upcoming" if upcoming else "daily")
    log(f"START meeting digest ({selected_mode})")
    log(f"Mail source: {selected_source_name()}")
    log("Fetching recent meeting candidates")
    try:
        records = fetch_mail()
    except MailSourceConfigurationError as exc:
        log(f"Mail source configuration error: {exc}")
        log("DONE meeting digest (FAILED)")
        return 1
    if records is None:
        log("Failed to retrieve data.")
        log("DONE meeting digest (FAILED)")
        return 1

    log(f"Processed {len(records)} messages for {TARGET_EMAIL}")
    now = local_now()
    today = now.date()
    reminder_meetings = []
    sent_reminders = {}
    if selected_mode == "reminder":
        try:
            lead_minutes = reminder_minutes()
            window_minutes = reminder_window_minutes()
        except ValueError as exc:
            log(f"Reminder configuration error: {exc}")
            log("DONE meeting digest (FAILED)")
            return 1
        due = due_reminder_meetings(
            records,
            now=now,
            lead_minutes=lead_minutes,
            window_minutes=window_minutes,
            since=load_last_successful_reminder_scan(now=now),
        )
        sent_reminders = load_sent_reminders(now=now)
        reminder_meetings = [
            meeting for meeting in due if reminder_key(meeting) not in sent_reminders
        ]
        if not reminder_meetings:
            if not dry_run:
                try:
                    mark_reminder_scan_succeeded(scan_at=now)
                except OSError as exc:
                    log(f"Reminder scan state could not be saved: {exc}")
                    log("DONE meeting digest (FAILED)")
                    return 1
            log("No unsent meeting reminders are due")
            log("DONE meeting digest")
            return 0
        message = format_reminder_digest(reminder_meetings, now=now)
        meeting_count = len(reminder_meetings)
        log(f"Found {meeting_count} due meeting reminders")
    elif selected_mode == "weekly":
        message = format_weekly_digest(records, today)
        meeting_count = len(_collect_meetings(records, today, today + timedelta(days=6)))
        log(f"Found {meeting_count} meetings in the 7-day view")
    elif selected_mode == "attention":
        message = format_attention_digest(records, today)
        meeting_count = len(_collect_meetings(records, today))
        log(f"Checked {meeting_count} upcoming meetings for attention signals")
    elif selected_mode == "upcoming":
        message = format_upcoming_digest(records, today)
        meeting_count = len(_collect_meetings(records, today))
        log(f"Found {meeting_count} meetings from {today.isoformat()} onward")
    elif selected_mode == "daily":
        message = format_digest(records, today)
        meeting_count = len(_collect_meetings(records, today, today))
        log(f"Found {meeting_count} meetings for {today.isoformat()}")
    else:
        log(f"Unknown digest mode: {selected_mode}")
        log("DONE meeting digest (FAILED)")
        return 1

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
            log("DONE meeting digest (FAILED)")
            return 1

        if selected_mode == "reminder":
            try:
                mark_reminders_sent(
                    sent_reminders,
                    reminder_meetings,
                    now=now,
                    last_successful_scan=now,
                )
            except OSError as exc:
                log(f"Reminder state could not be saved: {exc}")
                log("DONE meeting digest (FAILED)")
                return 1

    log("DONE meeting digest")
    return 0


def run_digest(upcoming=False, dry_run=False, mode=None):
    """Run one digest while holding the shared process-level lock."""

    with digest_lock():
        kwargs = {"upcoming": upcoming, "dry_run": dry_run}
        if mode is not None:
            kwargs["mode"] = mode
        return _run_digest(**kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but do not send")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--upcoming",
        action="store_true",
        help="List meetings scheduled today or later",
    )
    output_group.add_argument(
        "--week",
        action="store_true",
        help="List meetings for today and the following six days",
    )
    output_group.add_argument(
        "--attention",
        action="store_true",
        help="List upcoming meetings that require attention",
    )
    output_group.add_argument(
        "--reminder",
        action="store_true",
        help="Send reminders due in the configured reminder window",
    )
    args = parser.parse_args()

    try:
        mode = None
        if args.week:
            mode = "weekly"
        elif args.attention:
            mode = "attention"
        elif args.reminder:
            mode = "reminder"
        if mode is None:
            return run_digest(upcoming=args.upcoming, dry_run=args.dry_run)
        return run_digest(dry_run=args.dry_run, mode=mode)
    except DigestAlreadyRunning:
        # A concurrent launch is a normal no-op for launchd. The Telegram
        # listener calls run_digest() directly and handles this exception so
        # it can show the user a useful status message.
        log("Digest is already running; skipping this invocation")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
