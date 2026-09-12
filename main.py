"""Backward-compatible command facade for the mail digest application.

Implementation belongs to the layered :mod:`mail_digest` package. This module
keeps the historical ``python main.py`` entry point and the small public helper
surface used by older integrations; private parser helpers stay in their own
modules.
"""

from mail_digest.config import (
    APPLE_SCRIPT_TIMEOUT_SECONDS,
    CALENDAR_MARKERS,
    ENGLISH_MONTHS,
    ENV_FILE,
    FIELD_DELIMITER,
    ICS_MARKERS,
    LOCAL_TIMEZONE_NAME,
    MEETING_KEYWORDS,
    MONTHS,
    PROJECT_ROOT,
    SEMANTIC_CANCELLED_RE,
    SEMANTIC_RESCHEDULED_RE,
    SEMANTIC_TENTATIVE_RE,
    SCRIPT_PATH,
    TARGET_EMAIL,
    TRANSPORT_NEWLINE_TOKEN,
    TURKISH_MONTHS,
    YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS,
    load_env,
    log,
)
from mail_digest.delivery.telegram import send_telegram
from mail_digest.models import Meeting, MeetingOccurrence, MeetingStatus
from mail_digest.parsing.dates import parse_received_date
from mail_digest.parsing.ics import parse_ics_meetings
from mail_digest.parsing.meeting_parser import extract_meeting, extract_meetings
from mail_digest.services.meeting_service import (
    attention_reasons,
    due_reminder_meetings,
    format_attention_digest,
    format_date,
    format_digest,
    format_reminder_digest,
    format_upcoming_digest,
    format_weekly_digest,
)
from mail_digest.sources import fetch_mail
from mail_digest.utils import (
    limit_utf8_bytes,
    record_source_received_at,
    restore_transport_newlines,
    sanitize,
    sanitize_content,
    sanitize_transport_field,
    strip_quoted_reply,
)
from mail_digest.cli import main


__all__ = [
    "APPLE_SCRIPT_TIMEOUT_SECONDS",
    "CALENDAR_MARKERS",
    "ENGLISH_MONTHS",
    "ENV_FILE",
    "FIELD_DELIMITER",
    "ICS_MARKERS",
    "LOCAL_TIMEZONE_NAME",
    "MEETING_KEYWORDS",
    "MONTHS",
    "PROJECT_ROOT",
    "SEMANTIC_CANCELLED_RE",
    "SEMANTIC_RESCHEDULED_RE",
    "SEMANTIC_TENTATIVE_RE",
    "SCRIPT_PATH",
    "TARGET_EMAIL",
    "TRANSPORT_NEWLINE_TOKEN",
    "TURKISH_MONTHS",
    "YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS",
    "load_env",
    "log",
    "send_telegram",
    "Meeting",
    "MeetingOccurrence",
    "MeetingStatus",
    "parse_received_date",
    "parse_ics_meetings",
    "extract_meeting",
    "extract_meetings",
    "attention_reasons",
    "due_reminder_meetings",
    "format_attention_digest",
    "format_date",
    "format_digest",
    "format_reminder_digest",
    "format_upcoming_digest",
    "format_weekly_digest",
    "fetch_mail",
    "limit_utf8_bytes",
    "record_source_received_at",
    "restore_transport_newlines",
    "sanitize",
    "sanitize_content",
    "sanitize_transport_field",
    "strip_quoted_reply",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
