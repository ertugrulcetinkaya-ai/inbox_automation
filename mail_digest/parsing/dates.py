"""Date extraction and relative-date resolution."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from ..config import (
    MONTHS,
    YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS,
)
from ..utils import sanitize


MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))

ISO_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})(?!\d)"
)
NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<first>\d{1,2})[./-](?P<second>\d{1,2})"
    r"(?:[./-](?P<year>20\d{2}))?(?!\d)"
)
NUMERIC_DOT_TOKEN_RE = re.compile(
    r"(?<!\d)(?P<first>\d{1,2})\.(?P<second>\d{1,2})"
    r"(?:\.(?P<year>20\d{2}))?(?!\d)"
)
DAY_MONTH_DATE_RE = re.compile(
    rf"(?<!\w)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[\s,.-]+"
    rf"(?P<month>{MONTH_PATTERN})(?:[\s,.-]+(?P<year>20\d{{2}}))?(?!\w)",
    re.IGNORECASE,
)
MONTH_DAY_DATE_RE = re.compile(
    rf"(?<!\w)(?P<month>{MONTH_PATTERN})[\s,.-]+"
    rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:[\s,.-]+(?P<year>20\d{{2}}))?(?!\w)",
    re.IGNORECASE,
)

RELATIVE_DATE_RE = re.compile(r"\b(today|today's|tomorrow|bugün|yarın)\b", re.IGNORECASE)
WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
    "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
}
WEEKDAY_PREFIXES = "bu|this|önümüzdeki|onumuzdeki|gelecek|next"
WEEKDAY_RE = re.compile(
    r"(?<!\w)(?:(?P<prefix>" + WEEKDAY_PREFIXES + r")\s+)?"
    r"(?P<weekday>" + "|".join(sorted(WEEKDAY_NAMES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
DATE_CONTEXT_RE = re.compile(
    r"\b(?:tarih(?:i|inde|ine|li)?|gün(?:ü|ünde)?|gun(?:u|unde)?|date|dated|on)\b",
    re.IGNORECASE,
)
TIME_CONTEXT_RE = re.compile(r"\b(?:saat(?:inde)?|at|time)\b", re.IGNORECASE)
_DEFAULT_RELATIVE_ANCHOR = object()


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_yearless_date(month, day, target_date):
    """Resolve a month/day date relative to the digest target date."""
    current_candidate = _safe_date(target_date.year, month, day)
    if current_candidate is None:
        return _safe_date(target_date.year + 1, month, day)

    days_past = (target_date - current_candidate).days
    if days_past >= YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS:
        next_candidate = _safe_date(target_date.year + 1, month, day)
        if next_candidate is not None:
            return next_candidate
    return current_candidate


def _resolve_weekday_date(weekday, relative_date, prefix=""):
    """Resolve a weekday to the next practical occurrence."""
    days_until = (weekday - relative_date.weekday()) % 7
    if prefix.casefold() in {"önümüzdeki", "onumuzdeki", "gelecek", "next"} and days_until == 0:
        days_until = 7
    return relative_date + timedelta(days=days_until)


def _numeric_dot_token_kind(text, match):
    """Classify one dotted numeric token before date/time extraction."""
    first = int(match.group("first"))
    second = int(match.group("second"))
    if match.group("year"):
        return "date"

    before_start = max(0, match.start() - 24)
    after_end = min(len(text), match.end() + 24)
    nearby_before_start = max(0, match.start() - 12)
    nearby_after_end = min(len(text), match.end() + 12)

    def has_context(pattern, start, end):
        return any(
            start <= context.start() and context.end() <= end
            for context in pattern.finditer(text)
        )

    if has_context(DATE_CONTEXT_RE, nearby_before_start, match.start()) or has_context(
        DATE_CONTEXT_RE, match.end(), nearby_after_end
    ):
        return "date"
    if has_context(TIME_CONTEXT_RE, nearby_before_start, match.start()):
        return "time"
    if has_context(DATE_CONTEXT_RE, before_start, match.start()) or has_context(
        DATE_CONTEXT_RE, match.end(), after_end
    ):
        return "date"
    if first > 12 and second <= 12:
        return "date"
    if first <= 23 and second > 12:
        return "time"
    if first > 23:
        return "date"
    return "time"


def _numeric_dot_token_kind_at(text, start):
    match = NUMERIC_DOT_TOKEN_RE.match(text, start)
    if match is None:
        return None
    return _numeric_dot_token_kind(text, match)


def _date_hits(text, target_date, relative_date=_DEFAULT_RELATIVE_ANCHOR):
    hits = []

    if relative_date is _DEFAULT_RELATIVE_ANCHOR:
        relative_date = target_date

    def add_hit(match, year, month, day):
        if year is None:
            parsed = _resolve_yearless_date(int(month), int(day), target_date)
        else:
            parsed = _safe_date(int(year), int(month), int(day))
        if parsed is not None:
            hits.append({"date": parsed, "start": match.start(), "end": match.end()})

    def is_time_hour_suffix(match):
        return re.match(r":\d{2}\b", text[match.end():]) is not None

    for match in ISO_DATE_RE.finditer(text):
        add_hit(match, match.group("year"), match.group("month"), match.group("day"))

    for match in DAY_MONTH_DATE_RE.finditer(text):
        if is_time_hour_suffix(match):
            continue
        add_hit(
            match,
            match.group("year"),
            MONTHS[match.group("month").casefold()],
            match.group("day"),
        )

    for match in MONTH_DAY_DATE_RE.finditer(text):
        if is_time_hour_suffix(match):
            continue
        add_hit(
            match,
            match.group("year"),
            MONTHS[match.group("month").casefold()],
            match.group("day"),
        )

    for match in NUMERIC_DATE_RE.finditer(text):
        if "." in match.group(0) and _numeric_dot_token_kind(text, match) == "time":
            continue
        first = int(match.group("first"))
        second = int(match.group("second"))
        if first > 12 and second <= 12:
            day, month = first, second
        elif second > 12 and first <= 12:
            month, day = first, second
        else:
            day, month = first, second
        add_hit(match, match.group("year"), month, day)

    for match in RELATIVE_DATE_RE.finditer(text):
        if relative_date is None:
            continue
        word = match.group(1).casefold()
        resolved_date = relative_date + timedelta(days=1) if word in {"tomorrow", "yarın"} else relative_date
        hits.append({"date": resolved_date, "start": match.start(), "end": match.end()})

    for match in WEEKDAY_RE.finditer(text):
        if relative_date is None:
            continue
        weekday = WEEKDAY_NAMES[match.group("weekday").casefold()]
        resolved_date = _resolve_weekday_date(
            weekday,
            relative_date,
            match.group("prefix") or "",
        )
        hits.append({"date": resolved_date, "start": match.start(), "end": match.end()})

    unique = {}
    for hit in hits:
        unique[(hit["date"], hit["start"], hit["end"])] = hit
    return sorted(unique.values(), key=lambda hit: hit["start"])


def parse_received_date(value):
    """Parse the date string emitted by Apple Mail's date as string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = sanitize(value)
    if not text:
        return None

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed.date()
    except (TypeError, ValueError, OverflowError):
        pass

    fallback = datetime.now().date()
    explicit_hits = _date_hits(text, fallback, relative_date=None)
    return explicit_hits[0]["date"] if explicit_hits else None
