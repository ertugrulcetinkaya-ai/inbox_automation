"""Application configuration and shared domain constants."""

from __future__ import annotations

import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "mail_fetcher.applescript"
FIELD_DELIMITER = "__MAIL_DIGEST_FIELD__"
TRANSPORT_NEWLINE_TOKEN = "__MAIL_DIGEST_LINEBREAK__"
LOCAL_TIMEZONE_NAME = "Europe/Istanbul"
YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS = 60
# Apple Mail can take several seconds per body when it has to hydrate a
# remote/Gmail message. The fetcher already bounds the candidate set, but a
# cold Mail process can still need more than one minute to finish safely.
APPLE_SCRIPT_TIMEOUT_SECONDS = 180
# Gmail content is bounded before it reaches semantic parsing or the SQLite
# cache. Calendar payloads are much smaller in normal use; a separate limit
# keeps a malformed MIME part from turning into an unbounded cache entry.
MAX_BODY_BYTES = 256 * 1024
MAX_RAW_MIME_BYTES = 1024 * 1024
MAX_ICS_BYTES = 256 * 1024
GMAIL_HTTP_TIMEOUT_SECONDS = 30
REMINDER_MINUTES_DEFAULT = 15
REMINDER_WINDOW_MINUTES_DEFAULT = 5
DAILY_LOCK_WAIT_SECONDS_DEFAULT = 300.0
PREFLIGHT_LOCK_WAIT_SECONDS_DEFAULT = 120.0
ATTENTION_CONFIDENCE_THRESHOLD = 0.80
MAIL_SOURCE_DEFAULT = "gmail"
GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
GMAIL_DATA_DIR = Path.home() / ".hermes_local_automation" / "gmail"


class SecretFilePermissionError(PermissionError):
    """A credentials file is readable by another local user."""


class TargetEmailConfigurationError(ValueError):
    """The target Gmail account is missing or invalid."""


def gmail_credentials_file():
    return Path(
        os.environ.get("GMAIL_CREDENTIALS_FILE", str(GMAIL_DATA_DIR / "credentials.json"))
    ).expanduser()


def gmail_token_file():
    return Path(
        os.environ.get("GMAIL_TOKEN_FILE", str(GMAIL_DATA_DIR / "token.json"))
    ).expanduser()


def gmail_cache_file():
    return Path(
        os.environ.get("GMAIL_CACHE_FILE", str(GMAIL_DATA_DIR / "cache.sqlite3"))
    ).expanduser()


def telegram_env_file():
    return Path(
        os.environ.get(
            "TELEGRAM_ENV_FILE",
            str(Path.home() / ".hermes_local_automation" / "telegram.env"),
        )
    ).expanduser()


def digest_lock_file():
    return Path(
        os.environ.get("MAIL_DIGEST_LOCK_FILE", "/tmp/mail_unread_digest.lock")
    ).expanduser()


def reminder_state_file():
    return Path(
        os.environ.get(
            "MEETING_REMINDER_STATE_FILE",
            str(Path.home() / ".hermes_local_automation" / "mail_digest" / "reminders.json"),
        )
    ).expanduser()


def target_env_file():
    configured = os.environ.get("MAIL_DIGEST_TARGET_ENV_FILE")
    return Path(
        configured
        or Path.home() / ".hermes_local_automation" / "mail_digest" / "target.env"
    ).expanduser()


def _validated_target_email(value):
    email = value.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise TargetEmailConfigurationError(
            "MAIL_DIGEST_TARGET_EMAIL must contain a valid email address"
        )
    return email


def target_email():
    """Resolve the target account from the environment or its private env file."""

    if "MAIL_DIGEST_TARGET_EMAIL" in os.environ:
        return _validated_target_email(os.environ["MAIL_DIGEST_TARGET_EMAIL"])

    env_path = target_env_file()
    try:
        mode = stat.S_IMODE(env_path.stat().st_mode)
    except FileNotFoundError as exc:
        raise TargetEmailConfigurationError(
            "MAIL_DIGEST_TARGET_EMAIL is not configured; target env file is missing"
        ) from exc
    except OSError as exc:
        raise TargetEmailConfigurationError(
            "MAIL_DIGEST_TARGET_EMAIL target env file cannot be inspected"
        ) from exc
    if mode != 0o600:
        raise TargetEmailConfigurationError(
            "MAIL_DIGEST_TARGET_EMAIL target env file must have 0600 permissions"
        )

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TargetEmailConfigurationError(
            "MAIL_DIGEST_TARGET_EMAIL target env file cannot be read"
        ) from exc
    if len(lines) != 1:
        raise TargetEmailConfigurationError(
            "target env file must contain exactly one MAIL_DIGEST_TARGET_EMAIL line"
        )
    key, separator, value = lines[0].partition("=")
    if key != "MAIL_DIGEST_TARGET_EMAIL" or not separator:
        raise TargetEmailConfigurationError(
            "target env file must contain exactly one MAIL_DIGEST_TARGET_EMAIL line"
        )
    return _validated_target_email(value)

TURKISH_MONTHS = {
    "ocak": 1,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "eylül": 9,
    "ekim": 10,
    "kasım": 11,
    "aralık": 12,
}

ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

MONTHS = {**TURKISH_MONTHS, **ENGLISH_MONTHS}
MONTHS.update({
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
})

MEETING_KEYWORDS = (
    "toplantı", "toplanti", "meeting", "appointment", "randevu", "görüşme",
    "gorusme", "etkinlik", "event", "conference", "konferans", "seminar",
    "webinar", "interview", "mülakat", "mulakat", "invitation",
    "invite", "invited", "calendar", "takvim", "davetiye", "schedule",
    "planlama", "zoom", "webex", "google meet", "microsoft teams",
)

CALENDAR_MARKERS = (
    "when:", "where:", "organizer:", "attendees:", "join meeting",
    "join us", ".ics", "icalendar", "add to calendar", "takvime ekle",
)

# Subject-specific Turkish calendar invitation prefix. Matched only on the
# normalized subject (not searched across the body) so ordinary prose such as a
# "davet mektubu" does not become a false positive. This is a semantic signal
# only; an authoritative ICS result never depends on it.
CALENDAR_SUBJECT_PREFIXES = (
    "davet:",
)

ICS_MARKERS = (
    "begin:vcalendar", "begin:vevent", "text/calendar", "content-type: text/calendar",
    ".ics", "method:request", "method:publish", "method:cancel",
)

SEMANTIC_CANCELLED_RE = re.compile(
    r"\b(?:iptal\w*|cancel(?:led|ed|lation)?|canceled)\b", re.IGNORECASE
)
SEMANTIC_RESCHEDULED_RE = re.compile(
    r"(?:ertelen\w*|reschedul\w*|postpon\w*|yeniden\s+planlan\w*)",
    re.IGNORECASE,
)
SEMANTIC_TENTATIVE_RE = re.compile(
    r"\b(?:tentative|taslak|geçici|beklemede)\b", re.IGNORECASE
)


def log(message):
    timestamp = local_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def local_now():
    """Return the configured digest timezone as a naive local datetime."""

    return datetime.now(ZoneInfo(LOCAL_TIMEZONE_NAME)).replace(tzinfo=None)


def load_env(env_file=None):
    """Load the Telegram environment file without exposing its contents."""

    env_path = Path(env_file or telegram_env_file()).expanduser()
    env = {}
    if not env_path.is_file():
        raise FileNotFoundError(f"Env file not found at {env_path}")
    try:
        mode = stat.S_IMODE(env_path.stat().st_mode)
    except OSError as exc:
        raise SecretFilePermissionError(f"Cannot inspect env file permissions: {env_path}") from exc
    if mode & 0o077:
        raise SecretFilePermissionError(
            f"Env file must be owner-readable only (0600): {env_path}"
        )
    with env_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            env[key] = value.strip().strip("'\"")
    return env


def _positive_int_env(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bounded_int_env(name, default, minimum, maximum):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be between {minimum} and {maximum}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _nonnegative_float_env(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


def reminder_minutes():
    return _positive_int_env("MEETING_REMINDER_MINUTES", REMINDER_MINUTES_DEFAULT)


def reminder_window_minutes():
    return _positive_int_env(
        "MEETING_REMINDER_WINDOW_MINUTES",
        REMINDER_WINDOW_MINUTES_DEFAULT,
    )


def daily_lock_wait_seconds():
    return _nonnegative_float_env(
        "MAIL_DIGEST_DAILY_LOCK_WAIT_SECONDS",
        DAILY_LOCK_WAIT_SECONDS_DEFAULT,
    )


def preflight_lock_wait_seconds():
    value = _nonnegative_float_env(
        "MAIL_DIGEST_PREFLIGHT_LOCK_WAIT_SECONDS",
        PREFLIGHT_LOCK_WAIT_SECONDS_DEFAULT,
    )
    if value > 120:
        raise ValueError("MAIL_DIGEST_PREFLIGHT_LOCK_WAIT_SECONDS must be at most 120")
    return value


def gmail_batch_size():
    return _bounded_int_env("GMAIL_BATCH_SIZE", 10, 1, 50)


def gmail_batch_pause_seconds():
    return _nonnegative_float_env("GMAIL_BATCH_PAUSE_SECONDS", 1.0)


def gmail_retry_after_max_seconds():
    return _nonnegative_float_env("GMAIL_RETRY_AFTER_MAX_SECONDS", 60.0)


def gmail_retry_total_wait_seconds():
    return _nonnegative_float_env("GMAIL_RETRY_TOTAL_WAIT_SECONDS", 300.0)
