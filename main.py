import os
import subprocess
import requests
import warnings
import re
import argparse
import html
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as datetime_time, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from email import policy
from email.parser import Parser
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ provides zoneinfo
    ZoneInfo = None

# Suppress urllib3 NotOpenSSLWarning for launchd logs
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(
    os.environ.get(
        "TELEGRAM_ENV_FILE",
        str(Path.home() / ".hermes_local_automation/telegram.env"),
    )
).expanduser()
SCRIPT_PATH = PROJECT_ROOT / "mail_fetcher.applescript"
FIELD_DELIMITER = "__MAIL_DIGEST_FIELD__"
TARGET_EMAIL = "ertugrul@cetinkayalar.com"

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
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))

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
JOIN_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
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
TRANSPORT_NEWLINE_TOKEN = "__MAIL_DIGEST_LINEBREAK__"
LOCAL_TIMEZONE_NAME = "Europe/Istanbul"
YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS = 60

TR_OUTPUT_MONTHS = (
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)

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
TIME_WITH_COLON_RE = re.compile(
    r"(?<![\d.])(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
TIME_WITH_DOT_RE = re.compile(
    r"(?<![\d.])(?P<hour>\d{1,2})\.(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
TIME_WITH_AMPM_RE = re.compile(
    r"(?<!\w)(?P<hour>1[0-2]|[1-9])(?:[:.](?P<minute>\d{2}))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
RELATIVE_DATE_RE = re.compile(r"\b(today|today's|tomorrow|bugün|yarın)\b", re.IGNORECASE)
WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
    "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
}
WEEKDAY_RE = re.compile(r"\b(" + "|".join(sorted(WEEKDAY_NAMES, key=len, reverse=True)) + r")\b", re.IGNORECASE)
DATE_CONTEXT_RE = re.compile(
    r"\b(?:tarih(?:i|inde|ine|li)?|gün(?:ü|ünde)?|gun(?:u|unde)?|date|dated|on)\b",
    re.IGNORECASE,
)
TIME_CONTEXT_RE = re.compile(r"\b(?:saat(?:inde)?|at|time)\b", re.IGNORECASE)
_DEFAULT_RELATIVE_ANCHOR = object()


@dataclass(frozen=True)
class Meeting:
    """Canonical meeting model shared by ICS and semantic fallback parsing."""

    uid: str = ""
    title: str = ""
    organizer: str = ""
    start_at: object = None
    end_at: object = None
    timezone: str = ""
    location: str = ""
    join_url: str = ""
    status: str = "CONFIRMED"
    sequence: int = 0
    source_message_id: str = ""
    confidence: float = 0.0

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)

def load_env():
    env = {}
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Env file not found at {ENV_FILE}")
    with open(ENV_FILE, "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip("'\"")
    return env

def sanitize(text):
    if text is None:
        return ""
    # Replace CR/LF/TAB with spaces
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    # Remove ASCII control characters (0-31) except space
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch == " ")
    # Remove zero-width/invisible spam padding characters
    # \u200b: Zero Width Space, \u200c: Zero Width Non-Joiner, \u200d: Zero Width Joiner, \uFEFF: BOM
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    # Collapse repeated whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def restore_transport_newlines(text):
    return (text or "").replace(TRANSPORT_NEWLINE_TOKEN, "\n")


def sanitize_transport_field(text):
    return sanitize(restore_transport_newlines(text))


def sanitize_content(text):
    """Clean Mail content while preserving line structure required by ICS."""
    text = restore_transport_newlines(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\t")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text.strip()


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_yearless_date(month, day, target_date):
    """Resolve a month/day date relative to the digest target date.

    A yearless date normally belongs to the target year. If that candidate is
    at least the rollover threshold in the past, prefer the same date in the
    following year. This handles messages such as "5 Ocak" received on 20
    December without moving a recent past date such as "5 Ağustos" to next
    year.
    """
    current_candidate = _safe_date(target_date.year, month, day)
    if current_candidate is None:
        return _safe_date(target_date.year + 1, month, day)

    days_past = (target_date - current_candidate).days
    if days_past >= YEARLESS_DATE_ROLLOVER_THRESHOLD_DAYS:
        next_candidate = _safe_date(target_date.year + 1, month, day)
        if next_candidate is not None:
            return next_candidate
    return current_candidate


def _ics_unescape(value):
    value = html.unescape(value or "")
    value = value.replace("\\N", "\n").replace("\\n", "\n")
    value = value.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return value.strip()


def _unfold_ics_lines(text):
    lines = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_ics_property(line):
    if ":" not in line:
        return None
    name_and_params, value = line.split(":", 1)
    parts = name_and_params.split(";")
    name = parts[0].strip().upper()
    params = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, param_value = part.split("=", 1)
        params[key.strip().upper()] = param_value.strip().strip('"')
    return name, params, _ics_unescape(value)


def _parse_ics_datetime(value, params):
    value = (value or "").strip()
    if not value:
        return None, ""

    timezone_name = params.get("TZID", "").strip().strip('"')
    value_type = params.get("VALUE", "").upper()
    if value_type == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date(), timezone_name
        except ValueError:
            return None, timezone_name

    is_utc = value.endswith("Z")
    if is_utc:
        value = value[:-1]
        timezone_name = timezone_name or "UTC"

    parsed = None
    for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            parsed = datetime.strptime(value, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        return None, timezone_name

    if is_utc:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    elif timezone_name and ZoneInfo is not None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except Exception:
            pass
    return parsed, timezone_name


def _ics_first(properties, name):
    values = properties.get(name.upper(), [])
    return values[0] if values else ({}, "")


def _extract_join_url(values):
    urls = []
    for value in values:
        urls.extend(JOIN_URL_RE.findall(value or ""))
    cleaned = [url.rstrip(".,;:)]}") for url in urls]
    preferred_domains = (
        "teams.microsoft.com", "teams.live.com", "zoom.us", "webex.com",
        "meet.google.com",
    )
    for url in cleaned:
        if any(domain in url.casefold() for domain in preferred_domains):
            return url
    return cleaned[0] if cleaned else ""


def _ics_event_blocks(text):
    blocks = []
    current = None
    has_ics = False
    calendar_method = ""
    for line in _unfold_ics_lines(text):
        marker = line.strip().upper()
        if marker == "BEGIN:VCALENDAR":
            has_ics = True
        elif marker.startswith("METHOD:"):
            calendar_method = marker.split(":", 1)[1].strip()
        elif marker == "BEGIN:VEVENT":
            has_ics = True
            current = []
        elif marker == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return has_ics, calendar_method, blocks


def _normalize_ics_status(status, calendar_method):
    status = (status or "").strip().upper()
    calendar_method = (calendar_method or "").strip().upper()
    if calendar_method == "CANCEL" or status == "CANCELLED":
        return "CANCELLED"
    if status in {"TENTATIVE", "NEEDS-ACTION"}:
        return "TENTATIVE"
    if status == "RESCHEDULED":
        return "RESCHEDULED"
    return "CONFIRMED"


def _parse_ics_event(lines, record, calendar_method=""):
    properties = {}
    for line in lines:
        parsed = _parse_ics_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        properties.setdefault(name, []).append((params, value))

    _, summary = _ics_first(properties, "SUMMARY")
    _, uid = _ics_first(properties, "UID")
    organizer_params, organizer_value = _ics_first(properties, "ORGANIZER")
    _, location = _ics_first(properties, "LOCATION")
    _, description = _ics_first(properties, "DESCRIPTION")
    _, status = _ics_first(properties, "STATUS")
    _, sequence_value = _ics_first(properties, "SEQUENCE")
    _, url_value = _ics_first(properties, "URL")
    normalized_status = _normalize_ics_status(status, calendar_method)

    start_params, start_value = _ics_first(properties, "DTSTART")
    start_at, timezone_name = _parse_ics_datetime(start_value, start_params)
    if start_at is None and normalized_status == "CANCELLED":
        start_at = record.get("received_date")
        if not isinstance(start_at, (date, datetime)):
            start_at = parse_received_date(record.get("date", ""))
    if start_at is None:
        return None

    end_params, end_value = _ics_first(properties, "DTEND")
    end_at, end_timezone_name = _parse_ics_datetime(end_value, end_params)
    timezone_name = timezone_name or end_timezone_name

    organizer = organizer_params.get("CN", "") or organizer_value
    if organizer.casefold().startswith("mailto:"):
        organizer = organizer[7:]
    organizer = organizer or record.get("sender", "") or "Bilinmeyen gönderen"

    online_values = [description, location, url_value]
    for property_name, values in properties.items():
        if "URL" in property_name or property_name.startswith("X-MICROSOFT"):
            online_values.extend(value for _, value in values)

    source_message_id = record.get("source_message_id", "") or record.get("message_id", "")
    uid = uid or f"{source_message_id}:{start_at.isoformat()}"
    try:
        sequence = int(sequence_value or 0)
    except (TypeError, ValueError):
        sequence = 0
    return Meeting(
        uid=uid,
        title=summary or record.get("subject", "") or "Başlıksız toplantı",
        organizer=organizer,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone_name,
        location=location,
        join_url=_extract_join_url(online_values),
        status=normalized_status,
        sequence=sequence,
        source_message_id=source_message_id,
        confidence=1.0,
    )


def _ics_payloads_from_record(record):
    payloads = []
    detected = False
    content = record.get("content", "") or ""
    raw_source = record.get("raw_source", "") or ""

    if any(marker in content.casefold() for marker in ICS_MARKERS):
        detected = True
        payloads.append(content)

    if raw_source:
        raw_lower = raw_source.casefold()
        if any(marker in raw_lower for marker in ICS_MARKERS):
            detected = True
            payloads.append(raw_source)
        try:
            message = Parser(policy=policy.default).parsestr(raw_source)
            for part in message.walk():
                content_type = (part.get_content_type() or "").casefold()
                filename = (part.get_filename() or "").casefold()
                if content_type != "text/calendar" and not filename.endswith(".ics"):
                    continue
                detected = True
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload.decode(charset, errors="replace")
                elif payload is None:
                    payload = part.get_payload()
                if isinstance(payload, str):
                    payloads.append(payload)
        except (TypeError, ValueError):
            # A partial/truncated raw source can still contain an inline ICS.
            pass
    return detected, payloads


def parse_ics_meetings(record):
    """Return ICS meetings, or None when the record has no calendar payload."""
    detected, payloads = _ics_payloads_from_record(record)
    if not detected:
        return None

    meetings = []
    seen = set()
    for payload in payloads:
        _, calendar_method, blocks = _ics_event_blocks(payload)
        for block in blocks:
            meeting = _parse_ics_event(block, record, calendar_method)
            if meeting is None:
                continue
            key = (meeting.uid, meeting.start_at, meeting.title.casefold())
            existing = next((item for item in meetings if (
                item.uid, item.start_at, item.title.casefold()
            ) == key), None)
            if existing is None:
                meetings.append(meeting)
                seen.add(key)
            elif (
                meeting.sequence > existing.sequence
                or (
                    meeting.sequence == existing.sequence
                    and meeting.status == "CANCELLED"
                    and existing.status != "CANCELLED"
                )
            ):
                meetings[meetings.index(existing)] = meeting
    return meetings


def _meeting_display_start(meeting):
    start_at = meeting.start_at
    if isinstance(start_at, datetime) and start_at.tzinfo is not None and ZoneInfo is not None:
        try:
            return start_at.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME))
        except Exception:
            pass
    return start_at


def _meeting_display_date(meeting):
    start_at = _meeting_display_start(meeting)
    return start_at.date() if isinstance(start_at, datetime) else start_at


def _meeting_to_digest_record(meeting, position=0, date_override=None):
    start_at = _meeting_display_start(meeting)
    end_at = meeting.end_at
    if isinstance(end_at, datetime) and end_at.tzinfo is not None and ZoneInfo is not None:
        try:
            end_at = end_at.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME))
        except Exception:
            pass

    if isinstance(start_at, datetime):
        label = start_at.strftime("%H:%M")
        sort_minutes = start_at.hour * 60 + start_at.minute
        if isinstance(end_at, datetime) and end_at.date() == start_at.date():
            label = f"{label}–{end_at.strftime('%H:%M')}"
    else:
        label = "Tüm gün"
        sort_minutes = 24 * 60

    return {
        "subject": meeting.title,
        "sender": meeting.organizer or "Bilinmeyen gönderen",
        "date": _meeting_display_date(meeting) or date_override,
        "time": label,
        "sort_minutes": sort_minutes,
        "uid": meeting.uid,
        "organizer": meeting.organizer,
        "location": meeting.location,
        "join_url": meeting.join_url,
        "status": meeting.status,
        "sequence": meeting.sequence,
        "source_message_id": meeting.source_message_id,
        "confidence": meeting.confidence,
        "_position": position,
    }


def _numeric_dot_token_kind(text, match):
    """Classify one dotted numeric token before date/time extraction.

    A dotted token must not be independently interpreted as both a date and a
    time. Tokens with a year are dates. Otherwise, structurally date-like
    DD.MM tokens are dates unless nearby time wording makes the intent clear;
    HH.MM tokens are times unless nearby date wording makes the intent clear.
    Ambiguous tokens default to time because a date requires explicit context.
    """
    first = int(match.group("first"))
    second = int(match.group("second"))
    if match.group("year"):
        return "date"

    before = text[max(0, match.start() - 24):match.start()]
    after = text[match.end():min(len(text), match.end() + 24)]
    nearby_before = text[max(0, match.start() - 12):match.start()]
    nearby_after = text[match.end():min(len(text), match.end() + 12)]
    if TIME_CONTEXT_RE.search(nearby_before) or TIME_CONTEXT_RE.search(nearby_after):
        return "time"
    if DATE_CONTEXT_RE.search(before) or DATE_CONTEXT_RE.search(after):
        return "date"

    # 15.08 is much more plausibly DD.MM than 15:08 in a Turkish date digest.
    if first > 12 and second <= 12:
        return "date"
    # 10.30 must remain HH.MM; treating it as October 30 creates a false date.
    if first <= 23 and second > 12:
        return "time"
    if first > 23:
        return "date"

    # DD.MM is only a date when date context is present. Without that context,
    # an ambiguous two-part dotted token is safer as a time candidate.
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
        if "." in match.group(0):
            numeric_kind = _numeric_dot_token_kind(text, match)
            if numeric_kind == "time":
                continue
        first = int(match.group("first"))
        second = int(match.group("second"))
        if first > 12 and second <= 12:
            day, month = first, second
        elif second > 12 and first <= 12:
            month, day = first, second
        else:
            # The user's locale is Turkish, so ambiguous numeric dates are D/M/Y.
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
        weekday = WEEKDAY_NAMES[match.group(1).casefold()]
        if weekday == relative_date.weekday():
            hits.append({"date": relative_date, "start": match.start(), "end": match.end()})

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


def _time_hits(text):
    matches = []
    for pattern in (TIME_WITH_COLON_RE, TIME_WITH_DOT_RE, TIME_WITH_AMPM_RE):
        for match in pattern.finditer(text):
            if _numeric_dot_token_kind_at(text, match.start()) == "date":
                continue
            matches.append(match)

    unique = {}
    for match in matches:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        ampm = (match.group("ampm") or "").replace(".", "").lower()

        if ampm:
            if hour == 12:
                hour = 0
            if ampm == "pm":
                hour += 12
        if hour > 23 or minute > 59:
            continue

        # Do not interpret the middle of a numeric date such as 2026.08.14 as a time.
        if match.start() > 0 and text[match.start() - 1].isdigit():
            continue
        if match.end() < len(text) and text[match.end()] == ".":
            following = text[match.end() + 1:match.end() + 5]
            if following.isdigit() and len(following) == 4:
                continue

        unique[(match.start(), match.end())] = {
            "start": match.start(),
            "end": match.end(),
            "minutes": hour * 60 + minute,
            "label": f"{hour:02d}:{minute:02d}",
        }
    return sorted(unique.values(), key=lambda item: item["start"])


def _time_for_date(text, date_hit):
    window_start = max(0, date_hit["start"] - 260)
    window_end = min(len(text), date_hit["end"] + 260)
    nearby = [
        item for item in _time_hits(text[window_start:window_end])
        if abs((item["start"] + window_start) - date_hit["start"]) <= 260
    ]
    if not nearby:
        return None

    for item in nearby:
        item["start"] += window_start
        item["end"] += window_start

    after_date = [item for item in nearby if item["start"] >= date_hit["end"]]
    selected = after_date[:2] if after_date else nearby[-2:]
    if not selected:
        return None

    label = selected[0]["label"]
    end_minutes = None
    if len(selected) > 1:
        between = text[selected[0]["end"]:selected[1]["start"]].casefold()
        if re.search(r"[-–—]|\b(to|until|ile|kadar)\b", between):
            label = f"{label}–{selected[1]['label']}"
            end_minutes = selected[1]["minutes"]
    return {"label": label, "minutes": selected[0]["minutes"], "end_minutes": end_minutes}


def _is_meeting_message(subject, content):
    haystack = f"{subject}\n{content}".casefold()
    return any(keyword in haystack for keyword in MEETING_KEYWORDS + CALENDAR_MARKERS)


def _received_date_for_record(record, fallback_date):
    if "received_date" in record:
        received_date = record.get("received_date")
        if not isinstance(received_date, (date, datetime)):
            received_date = parse_received_date(received_date)
    elif "date" in record:
        received_date = parse_received_date(record.get("date"))
    else:
        # Preserve helper behavior for synthetic records without transport
        # metadata; Apple Mail records always carry date.
        received_date = fallback_date
    if isinstance(received_date, datetime):
        received_date = received_date.date()
    return received_date


def _semantic_status(text):
    """Infer lifecycle state from human-written cancellation/update language."""
    if SEMANTIC_RESCHEDULED_RE.search(text):
        return "RESCHEDULED"
    if SEMANTIC_CANCELLED_RE.search(text):
        return "CANCELLED"
    if SEMANTIC_TENTATIVE_RE.search(text):
        return "TENTATIVE"
    return "CONFIRMED"


def _semantic_date_hits_for_status(text, date_hits, status):
    if status == "CANCELLED":
        return []
    if status == "RESCHEDULED" and len(date_hits) > 1:
        # In Turkish/English reschedule notices the new date is normally the
        # last date mentioned: "15 Ağustos ... 18 Ağustos'a ertelendi".
        return [date_hits[-1]]
    return date_hits


def _semantic_meeting_from_date_hit(record, subject, text, date_hit, status):
    time_info = _time_for_date(text, date_hit)
    start_at = date_hit["date"]
    end_at = None
    if time_info:
        start_at = datetime.combine(
            date_hit["date"],
            datetime_time(time_info["minutes"] // 60, time_info["minutes"] % 60),
        )
        if time_info.get("end_minutes") is not None:
            end_at = datetime.combine(
                date_hit["date"],
                datetime_time(
                    time_info["end_minutes"] // 60,
                    time_info["end_minutes"] % 60,
                ),
            )
    return Meeting(
        title=subject or "Başlıksız toplantı",
        organizer=record.get("sender", "") or "Bilinmeyen gönderen",
        start_at=start_at,
        end_at=end_at,
        status=status,
        source_message_id=record.get("source_message_id", "") or record.get("message_id", ""),
        confidence=0.70 if status == "RESCHEDULED" else 0.55,
    )


def extract_meetings(record, start_date, end_date=None, include_cancelled=False):
    subject = record.get("subject", "")
    content = record.get("content", record.get("snippet", ""))
    received_date = _received_date_for_record(record, start_date)

    # Calendar data is authoritative. Do this before subject/keyword filtering
    # so an invitation with a neutral subject is still recognized.
    ics_meetings = parse_ics_meetings(record)
    if ics_meetings is not None:
        digest_records = []
        for index, meeting in enumerate(ics_meetings):
            meeting_date = _meeting_display_date(meeting)
            if meeting.status == "CANCELLED":
                if include_cancelled:
                    digest_records.append(
                        _meeting_to_digest_record(
                            meeting,
                            position=index,
                            date_override=received_date,
                        )
                    )
                continue
            if meeting_date is None or meeting_date < start_date:
                continue
            if end_date is not None and meeting_date > end_date:
                continue
            digest_records.append(_meeting_to_digest_record(meeting, position=index))
        return digest_records

    if not _is_meeting_message(subject, content):
        return []

    text = f"{subject}\n{content}"
    status = _semantic_status(text)
    date_hits = _date_hits(text, start_date, relative_date=received_date)
    matching_dates = [
        hit
        for hit in _semantic_date_hits_for_status(text, date_hits, status)
        if hit["date"] >= start_date and (end_date is None or hit["date"] <= end_date)
    ]
    if status == "CANCELLED" and include_cancelled and not matching_dates:
        if date_hits:
            matching_dates = [date_hits[0]]
        else:
            return [
                _meeting_to_digest_record(
                    Meeting(
                        title=subject or "Başlıksız toplantı",
                        organizer=record.get("sender", "") or "Bilinmeyen gönderen",
                        start_at=received_date or start_date,
                        status="CANCELLED",
                        source_message_id=record.get("source_message_id", ""),
                        confidence=0.70,
                    ),
                    date_override=received_date or start_date,
                )
            ]
    return [
        _meeting_to_digest_record(
            _semantic_meeting_from_date_hit(record, subject, text, date_hit, status),
            position=date_hit["start"],
            date_override=received_date,
        )
        for date_hit in matching_dates
    ]


def extract_meeting(record, target_date):
    meetings = extract_meetings(record, target_date, target_date)
    if not meetings:
        return None
    meeting = min(meetings, key=lambda item: (item["sort_minutes"], item["_position"]))
    meeting.pop("_position", None)
    return meeting

def fetch_mail():
    try:
        result = subprocess.run(
            ["osascript", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        
        # Combine stdout and stderr because 'log' in AppleScript often goes to stderr
        raw_output = result.stdout + "\n" + result.stderr
        
        records = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line or FIELD_DELIMITER not in line:
                continue
            
            # Remove AppleScript 'log:' prefix if present
            if line.startswith("log:"):
                line = line[4:].strip()
            
            parts = line.split(FIELD_DELIMITER)
            if len(parts) >= 6:
                received_text = sanitize_transport_field(parts[4])
                records.append({
                    "account": sanitize_transport_field(parts[0]),
                    "mailbox": sanitize_transport_field(parts[1]),
                    "sender": sanitize_transport_field(parts[2]),
                    "subject": sanitize_transport_field(parts[3]),
                    "date": received_text,
                    "received_date": parse_received_date(received_text),
                    "content": sanitize_content(parts[5]),
                    "source_message_id": sanitize_transport_field(parts[6]) if len(parts) >= 7 else "",
                    "raw_source": sanitize_content(parts[7]) if len(parts) >= 8 else "",
                })

        if result.returncode != 0 and not records:
            log(f"AppleScript error: {sanitize(result.stderr)}")
            return None
        
        return records
    except Exception as e:
        log(f"Error fetching mail: {e}")
        return None

def send_telegram(message):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        log("Missing Telegram credentials in env file.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
    
    success = True
    for idx, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code != 200:
                log(f"Telegram Error (Chunk {idx+1}/{len(chunks)}): {response.status_code}")
                success = False
        except Exception as e:
            log(f"Network error sending chunk {idx+1}: {e}")
            success = False
    return success

def format_date(target_date):
    return f"{target_date.day} {TR_OUTPUT_MONTHS[target_date.month]} {target_date.year}"


def _status_label(status):
    return {
        "RESCHEDULED": "Ertelendi",
        "TENTATIVE": "Kesinleşmedi",
    }.get(status, "")


def _collect_meetings(records, start_date, end_date=None):
    meetings = []
    for record in records:
        meetings.extend(
            extract_meetings(
                record,
                start_date,
                end_date,
                include_cancelled=True,
            )
        )

    status_priority = {
        "CONFIRMED": 0,
        "TENTATIVE": 1,
        "RESCHEDULED": 2,
        "CANCELLED": 3,
    }

    def subject_key(meeting):
        return re.sub(r"\W+", " ", meeting["subject"].casefold()).strip()

    def should_replace(current, candidate):
        if candidate.get("sequence", 0) != current.get("sequence", 0):
            return candidate.get("sequence", 0) > current.get("sequence", 0)
        return status_priority.get(candidate.get("status"), 0) >= status_priority.get(
            current.get("status"), 0
        )

    latest_by_uid = {}
    without_uid = []
    for meeting in meetings:
        uid = meeting.get("uid", "")
        if not uid:
            without_uid.append(meeting)
            continue
        current = latest_by_uid.get(uid)
        if current is None or should_replace(current, meeting):
            latest_by_uid[uid] = meeting

    resolved = list(latest_by_uid.values()) + without_uid
    cancelled_subjects = {
        subject_key(meeting)
        for meeting in resolved
        if meeting.get("status") == "CANCELLED"
    }
    rescheduled_subjects = {
        subject_key(meeting)
        for meeting in resolved
        if meeting.get("status") == "RESCHEDULED"
    }

    unique_meetings = {}
    for meeting in resolved:
        status = meeting.get("status", "CONFIRMED")
        if status == "CANCELLED":
            continue
        normalized_subject = subject_key(meeting)
        if status in {"CONFIRMED", "TENTATIVE"} and (
            normalized_subject in cancelled_subjects
            or normalized_subject in rescheduled_subjects
        ):
            continue
        key = (
            meeting.get("uid") or meeting["date"],
            meeting["time"],
            normalized_subject,
        )
        unique_meetings[key] = meeting
    return sorted(
        unique_meetings.values(),
        key=lambda item: (item["date"], item["sort_minutes"], item["subject"].casefold()),
    )


def format_digest(records, target_date=None):
    target_date = target_date or datetime.now().date()
    meetings = _collect_meetings(records, target_date, target_date)

    date_label = format_date(target_date)
    if not meetings:
        return f"📅 {date_label}\nBugün toplantı yok."

    lines = [f"📅 Bugünkü toplantılar — {date_label}", ""]
    for meeting in meetings:
        subject = meeting["subject"][:100]
        sender = meeting["sender"][:80]
        status_label = _status_label(meeting.get("status", "CONFIRMED"))
        status_suffix = f" [{status_label}]" if status_label else ""
        lines.append(f"• {meeting['time']} — {subject}{status_suffix}")
        lines.append(f"  Gönderen: {sender}")
        if meeting.get("location"):
            lines.append(f"  Yer: {meeting['location'][:160]}")
        if meeting.get("join_url"):
            lines.append(f"  Katılım: {meeting['join_url'][:300]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_upcoming_digest(records, start_date=None):
    start_date = start_date or datetime.now().date()
    meetings = _collect_meetings(records, start_date)

    if not meetings:
        return "📅 Bugün ve sonraki toplantılar\nBugün veya sonrasında toplantı yok."

    lines = ["📅 Bugün ve sonraki toplantılar", ""]
    current_date = None
    for meeting in meetings:
        if meeting["date"] != current_date:
            if current_date is not None:
                lines.append("")
            lines.append(format_date(meeting["date"]))
            current_date = meeting["date"]
        subject = meeting["subject"][:100]
        sender = meeting["sender"][:80]
        status_label = _status_label(meeting.get("status", "CONFIRMED"))
        status_suffix = f" [{status_label}]" if status_label else ""
        lines.append(f"• {meeting['time']} — {subject}{status_suffix}")
        lines.append(f"  Gönderen: {sender}")
        if meeting.get("location"):
            lines.append(f"  Yer: {meeting['location'][:160]}")
        if meeting.get("join_url"):
            lines.append(f"  Katılım: {meeting['join_url'][:300]}")
    return "\n".join(lines).rstrip()


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

if __name__ == "__main__":
    raise SystemExit(main())
