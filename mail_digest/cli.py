"""Command-line entry point for the daily digest."""

import argparse

from .config import (
    PROJECT_ROOT,
    daily_lock_wait_seconds,
    local_now,
    log,
    preflight_lock_wait_seconds,
    reminder_minutes,
    reminder_window_minutes,
)
from .delivery.telegram import send_telegram
from .services.meeting_service import (
    build_digest_result,
    due_reminder_meetings,
    format_reminder_digest,
)
from .services.lock import DigestAlreadyRunning, digest_lock
from .services.health import build_status_report
from .services.log_rotation import CLI_LOG_NAMES, rotate_logs
from .services.reminder_state import (
    load_last_successful_reminder_scan,
    load_sent_reminders,
    mark_reminder_scan_succeeded,
    mark_reminders_sent,
    reminder_key,
)
from .services.run_status import (
    record_run_finished,
    record_run_skipped_lock,
    record_run_started,
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

    log(f"Processed {len(records)} messages")
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
    elif selected_mode in {"weekly", "attention", "upcoming", "daily"}:
        digest_result = build_digest_result(records, selected_mode, target_date=today)
        message = digest_result.message
        meeting_count = digest_result.count
        if selected_mode == "weekly":
            log(f"Found {meeting_count} meetings in the 7-day view")
        elif selected_mode == "attention":
            log(f"Checked {meeting_count} upcoming meetings for attention signals")
        elif selected_mode == "upcoming":
            log(f"Found {meeting_count} meetings from {today.isoformat()} onward")
        else:
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


def run_digest(
    upcoming=False,
    dry_run=False,
    mode=None,
    wait_seconds: float = 0.0,
    scheduled=False,
):
    """Run one digest while holding the shared process-level lock."""

    selected_mode = mode or ("upcoming" if upcoming else "daily")
    if scheduled and selected_mode != "daily":
        raise ValueError("scheduled mode is only valid for the daily digest")
    status_mode = "daily_scheduled" if scheduled else selected_mode
    lock_wait = daily_lock_wait_seconds() if scheduled else wait_seconds
    try:
        with digest_lock(wait_seconds=lock_wait):
            if not dry_run:
                record_run_started(status_mode)
            kwargs = {"upcoming": upcoming, "dry_run": dry_run}
            if mode is not None:
                kwargs["mode"] = mode
            try:
                result = _run_digest(**kwargs)
            except Exception as exc:
                if not dry_run:
                    record_run_finished(
                        status_mode,
                        succeeded=False,
                        error_type=exc.__class__,
                    )
                raise
            if not dry_run:
                record_run_finished(
                    status_mode,
                    succeeded=result == 0,
                    error_type="DigestFailed" if result != 0 else None,
                )
            return result
    except DigestAlreadyRunning:
        if not scheduled:
            raise
        if not dry_run:
            record_run_skipped_lock(status_mode)
        log("Scheduled daily digest skipped because the digest lock stayed busy")
        return 75


def main():
    rotate_logs(PROJECT_ROOT / "logs", names=CLI_LOG_NAMES)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but do not send")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Mark this daily run as scheduled and wait for the shared lock",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--status",
        action="store_true",
        help="Show local runtime health or send it to Telegram",
    )
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

    if args.scheduled and (
        args.status or args.upcoming or args.week or args.attention or args.reminder
    ):
        parser.error("--scheduled can only be used with the daily digest")
    if args.scheduled:
        try:
            daily_lock_wait_seconds()
        except ValueError as exc:
            log(f"Scheduled daily lock configuration error: {exc}")
            return 1

    preflight_run = args.upcoming and args.dry_run
    preflight_wait_seconds = 0.0
    if preflight_run:
        try:
            preflight_wait_seconds = preflight_lock_wait_seconds()
        except ValueError as exc:
            log(f"Deploy preflight lock configuration error: {exc}")
            return 1

    if args.status:
        report = build_status_report()
        if args.dry_run:
            print(report)
            return 0
        return 0 if send_telegram(report) else 1

    try:
        mode = None
        if args.week:
            mode = "weekly"
        elif args.attention:
            mode = "attention"
        elif args.reminder:
            mode = "reminder"
        if mode is None:
            return run_digest(
                upcoming=args.upcoming,
                dry_run=args.dry_run,
                wait_seconds=preflight_wait_seconds,
                scheduled=args.scheduled,
            )
        return run_digest(dry_run=args.dry_run, mode=mode)
    except DigestAlreadyRunning:
        if preflight_run:
            log("Deployment preflight stopped because the production digest is running")
            return 75
        # A concurrent launch is a normal no-op for launchd. The Telegram
        # listener calls run_digest() directly and handles this exception so
        # it can show the user a useful status message.
        log("Digest is already running; skipping this invocation")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
