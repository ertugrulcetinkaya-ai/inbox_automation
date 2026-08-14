"""Backward-compatible entry point for the mail digest application.

The implementation lives in :mod:`mail_digest`. This facade keeps the
existing ``python main.py`` launchd contract and legacy imports stable while
allowing parser, source, service, and delivery code to evolve independently.
"""

from mail_digest.config import (
    CALENDAR_MARKERS,
    APPLE_SCRIPT_TIMEOUT_SECONDS,
    ENGLISH_MONTHS,
    ENV_FILE,
    FIELD_DELIMITER,
    ICS_MARKERS,
    LOCAL_TIMEZONE_NAME,
    MEETING_KEYWORDS,
    MONTHS,
    PROJECT_ROOT,
    SCRIPT_PATH,
    SEMANTIC_CANCELLED_RE,
    SEMANTIC_RESCHEDULED_RE,
    SEMANTIC_TENTATIVE_RE,
    TARGET_EMAIL,
    TRANSPORT_NEWLINE_TOKEN,
    TURKISH_MONTHS,
    YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS,
    load_env,
    log,
)
from mail_digest.delivery.telegram import send_telegram
from mail_digest.models import Meeting
from mail_digest.parsing.dates import (
    DATE_CONTEXT_RE,
    DAY_MONTH_DATE_RE,
    ISO_DATE_RE,
    MONTH_DAY_DATE_RE,
    MONTH_PATTERN,
    NUMERIC_DATE_RE,
    NUMERIC_DOT_TOKEN_RE,
    RELATIVE_DATE_RE,
    TIME_CONTEXT_RE,
    WEEKDAY_NAMES,
    WEEKDAY_PREFIXES,
    WEEKDAY_RE,
    _DEFAULT_RELATIVE_ANCHOR,
    _date_hits,
    _numeric_dot_token_kind,
    _numeric_dot_token_kind_at,
    _resolve_weekday_date,
    _resolve_yearless_date,
    _safe_date,
    parse_received_date,
)
from mail_digest.parsing.ics import (
    JOIN_URL_RE,
    _extract_join_url,
    _ics_event_blocks,
    _ics_first,
    _ics_payloads_from_record,
    _ics_unescape,
    _normalize_ics_status,
    _parse_ics_datetime,
    _parse_ics_event,
    _parse_ics_property,
    _unfold_ics_lines,
    parse_ics_meetings,
)
from mail_digest.parsing.meeting_parser import (
    _is_meeting_message,
    _meeting_display_date,
    _meeting_display_start,
    _meeting_to_digest_record,
    _received_date_for_record,
    _semantic_date_hits_for_status,
    _semantic_meeting_from_date_hit,
    _semantic_status,
    extract_meeting,
    extract_meetings,
)
from mail_digest.parsing.times import (
    TIME_WITH_AMPM_RE,
    TIME_WITH_COLON_RE,
    TIME_WITH_DOT_RE,
    _time_for_date,
    _time_hits,
)
from mail_digest.services.meeting_service import (
    TR_OUTPUT_MONTHS,
    _collect_meetings,
    _status_label,
    format_date,
    format_digest,
    format_upcoming_digest,
)
from mail_digest.sources.apple_mail import fetch_mail
from mail_digest.utils import (
    restore_transport_newlines,
    sanitize,
    sanitize_content,
    sanitize_transport_field,
)
from mail_digest.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
