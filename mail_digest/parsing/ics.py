"""iCalendar/ICS parsing."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone as dt_timezone
from email import policy
from email.parser import Parser

from ..config import ICS_MARKERS
from ..models import Meeting
from .dates import parse_received_date

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ provides zoneinfo
    ZoneInfo = None


JOIN_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


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
            pass
    return detected, payloads


def parse_ics_meetings(record):
    """Return ICS meetings, or None when the record has no calendar payload."""
    detected, payloads = _ics_payloads_from_record(record)
    if not detected:
        return None

    meetings = []
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
