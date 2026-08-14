"""Application configuration and shared domain constants."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(
    os.environ.get(
        "TELEGRAM_ENV_FILE",
        str(Path.home() / ".hermes_local_automation/telegram.env"),
    )
).expanduser()
SCRIPT_PATH = PROJECT_ROOT / "mail_fetcher.applescript"
FIELD_DELIMITER = "__MAIL_DIGEST_FIELD__"
TRANSPORT_NEWLINE_TOKEN = "__MAIL_DIGEST_LINEBREAK__"
TARGET_EMAIL = "ertugrul@cetinkayalar.com"
LOCAL_TIMEZONE_NAME = "Europe/Istanbul"
YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS = 60
APPLE_SCRIPT_TIMEOUT_SECONDS = 60

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
    "seminar", "webinar", "interview", "mülakat", "mulakat", "invitation",
    "invite", "invited", "calendar", "takvim", "davetiye", "schedule",
    "planlama", "zoom", "webex", "google meet", "microsoft teams",
)

CALENDAR_MARKERS = (
    "when:", "where:", "organizer:", "attendees:", "join meeting",
    "join us", ".ics", "icalendar", "add to calendar", "takvime ekle",
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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_env():
    env = {}
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Env file not found at {ENV_FILE}")
    with open(ENV_FILE, "r") as handle:
        for line in handle:
            if "=" in line and not line.startswith("#"):
                key, value = line.strip().split("=", 1)
                env[key] = value.strip("'\"")
    return env
