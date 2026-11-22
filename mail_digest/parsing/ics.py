"""iCalendar/ICS parsing."""

from __future__ import annotations

import html
import re
from dataclasses import replace
from datetime import date, datetime, time as dt_time, timedelta, timezone as dt_timezone
from email import policy
from email.parser import Parser

from dateutil.rrule import rrulestr

from ..config import ICS_MARKERS, LOCAL_TIMEZONE_NAME, local_now
from ..models import Meeting
from ..utils import record_source_received_at
from .dates import parse_received_date
from .lifecycle import lifecycle_should_replace


MAX_RECURRENCES_PER_SERIES = 500
DEFAULT_RECURRENCE_RANGE_DAYS = 60
UNRESOLVED_TZID_COUNT = 0
TIMEZONE_DEFINITIONS_PROPERTY = "_TIMEZONE_DEFINITIONS"

WINDOWS_TO_IANA = {
    "turkey": "Europe/Istanbul",
    "turkey standard time": "Europe/Istanbul",
    "gtb": "Europe/Bucharest",
    "gtb standard time": "Europe/Bucharest",
    "e. europe": "Europe/Chisinau",
    "e. europe standard time": "Europe/Chisinau",
    "w. europe": "Europe/Berlin",
    "w. europe standard time": "Europe/Berlin",
    "romance": "Europe/Paris",
    "romance standard time": "Europe/Paris",
    "central europe": "Europe/Budapest",
    "central europe standard time": "Europe/Budapest",
    "gmt": "Etc/GMT",
    "gmt standard time": "Europe/London",
    "utc": "Etc/UTC",
    "eastern": "America/New_York",
    "eastern standard time": "America/New_York",
    "central": "America/Chicago",
    "central standard time": "America/Chicago",
    "mountain": "America/Denver",
    "mountain standard time": "America/Denver",
    "pacific": "America/Los_Angeles",
    "pacific standard time": "America/Los_Angeles",
    "india": "Asia/Kolkata",
    "india standard time": "Asia/Kolkata",
    "china": "Asia/Shanghai",
    "china standard time": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo",
    "tokyo standard time": "Asia/Tokyo",
    "arabian": "Asia/Dubai",
    "arabian standard time": "Asia/Dubai",
    "russian standard time": "Europe/Moscow",
}

DURATION_RE = re.compile(
    r"^(?P<sign>[+-])?P(?:(?P<weeks>\d+)W|(?:(?P<days>\d+)D)?"
    r"(?:(?P<time>T)(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?)$",
    re.IGNORECASE,
)

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


def _split_quoted(value, delimiter, maxsplit=-1):
    parts = []
    quoted = False
    escaped = False
    start = 0
    splits = 0
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quoted:
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
            continue
        if character == delimiter and not quoted and (maxsplit < 0 or splits < maxsplit):
            parts.append(value[start:index])
            start = index + 1
            splits += 1
    parts.append(value[start:])
    return parts


def _parse_ics_property(line):
    property_parts = _split_quoted(line, ":", maxsplit=1)
    if len(property_parts) != 2:
        return None
    name_and_params, value = property_parts
    parts = _split_quoted(name_and_params, ";")
    name = parts[0].strip().upper()
    params = {}
    for part in parts[1:]:
        parameter_parts = _split_quoted(part, "=", maxsplit=1)
        if len(parameter_parts) != 2:
            continue
        key, param_value = parameter_parts
        params[key.strip().upper()] = param_value.strip().strip('"')
    return name, params, _ics_unescape(value)


def _parse_timezone_offset(value):
    match = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?", (value or "").strip())
    if match is None:
        return None
    sign = -1 if match.group(1) == "-" else 1
    hours, minutes = int(match.group(2)), int(match.group(3))
    seconds = int(match.group(4) or 0)
    if hours >= 24 or minutes >= 60 or seconds >= 60:
        return None
    offset = timedelta(hours=hours, minutes=minutes, seconds=seconds) * sign
    try:
        return dt_timezone(offset)
    except ValueError:
        return None


def _fixed_timezone_from_tzid(timezone_name):
    match = re.fullmatch(
        r"(?:UTC|GMT)([+-])(\d{1,2})(?::?(\d{2}))?(?::?(\d{2}))?",
        (timezone_name or "").strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    sign = -1 if match.group(1) == "-" else 1
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    if hours >= 24 or minutes >= 60 or seconds >= 60:
        return None
    try:
        return dt_timezone(timedelta(hours=hours, minutes=minutes, seconds=seconds) * sign)
    except ValueError:
        return None


def _ics_timezone_definitions(text):
    definitions = {}
    in_timezone = False
    in_standard = False
    timezone_name = ""
    standard_offset = None
    for line in _unfold_ics_lines(text):
        marker = line.strip().upper()
        if marker == "BEGIN:VTIMEZONE":
            in_timezone = True
            in_standard = False
            timezone_name = ""
            standard_offset = None
            continue
        if not in_timezone:
            continue
        if marker == "END:VTIMEZONE":
            if timezone_name and standard_offset is not None:
                definitions[timezone_name.casefold()] = standard_offset
            in_timezone = False
            in_standard = False
            continue
        if marker == "BEGIN:STANDARD":
            in_standard = True
            continue
        if marker in {"END:STANDARD", "END:DAYLIGHT"}:
            in_standard = False
            continue
        parsed = _parse_ics_property(line)
        if parsed is None:
            continue
        name, _, value = parsed
        if name == "TZID":
            timezone_name = value
        elif name == "TZOFFSETTO" and in_standard and standard_offset is None:
            standard_offset = _parse_timezone_offset(value)
    return definitions


def _resolve_ics_timezone(timezone_name, timezone_definitions=None):
    global UNRESOLVED_TZID_COUNT

    fixed_timezone = _fixed_timezone_from_tzid(timezone_name)
    if fixed_timezone is not None:
        return fixed_timezone

    normalized_name = timezone_name.casefold()
    iana_name = WINDOWS_TO_IANA.get(normalized_name)
    if iana_name is None and normalized_name.endswith(" standard time"):
        iana_name = WINDOWS_TO_IANA.get(normalized_name[: -len(" standard time")])
    if iana_name is not None and ZoneInfo is not None:
        try:
            return ZoneInfo(iana_name)
        except (KeyError, ValueError):
            pass
    if ZoneInfo is not None:
        try:
            return ZoneInfo(timezone_name)
        except (KeyError, ValueError):
            pass
    fallback = (timezone_definitions or {}).get(normalized_name)
    if fallback is not None:
        return fallback
    UNRESOLVED_TZID_COUNT += 1
    return None


def _parse_duration(value):
    match = DURATION_RE.fullmatch((value or "").strip())
    if match is None:
        return None
    groups = ("weeks", "days", "hours", "minutes", "seconds")
    if not any(match.group(name) is not None for name in groups):
        return None
    if match.group("time") and not any(
        match.group(name) is not None for name in ("hours", "minutes", "seconds")
    ):
        return None
    duration = timedelta(
        weeks=int(match.group("weeks") or 0),
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=int(match.group("seconds") or 0),
    )
    return -duration if match.group("sign") == "-" else duration


def _parse_ics_datetime(value, params, timezone_definitions=None):
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
    elif timezone_name:
        resolved_timezone = _resolve_ics_timezone(timezone_name, timezone_definitions)
        if resolved_timezone is not None:
            parsed = parsed.replace(tzinfo=resolved_timezone)
        else:
            timezone_name = ""
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
    nested_depth = 0
    has_ics = False
    calendar_method = ""
    for line in _unfold_ics_lines(text):
        marker = line.strip().upper()
        if marker == "BEGIN:VCALENDAR":
            has_ics = True
            # METHOD belongs to this VCALENDAR. Resetting it at each calendar
            # prevents a later METHOD:CANCEL from changing an earlier request
            # when providers concatenate multiple calendars in one message.
            calendar_method = ""
        elif marker.startswith("METHOD:"):
            calendar_method = marker.split(":", 1)[1].strip()
        elif marker == "BEGIN:VEVENT":
            has_ics = True
            current = []
            nested_depth = 0
        elif marker == "END:VEVENT":
            if current is not None and nested_depth == 0:
                blocks.append((calendar_method, current))
                current = None
            elif current is not None and nested_depth > 0:
                nested_depth -= 1
        elif current is not None:
            if marker.startswith("BEGIN:"):
                nested_depth += 1
            elif marker.startswith("END:"):
                if nested_depth:
                    nested_depth -= 1
            elif nested_depth == 0:
                current.append(line)
    return has_ics, blocks


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


def recurrence_identity(recurrence_id):
    """Normalize a recurrence id to a stable UTC instant or all-day date."""

    if isinstance(recurrence_id, datetime):
        if recurrence_id.tzinfo is None or recurrence_id.utcoffset() is None:
            recurrence_id = recurrence_id.replace(
                tzinfo=ZoneInfo(LOCAL_TIMEZONE_NAME)
                if ZoneInfo is not None
                else dt_timezone.utc
            )
        return recurrence_id.astimezone(dt_timezone.utc).isoformat()
    if isinstance(recurrence_id, date):
        return recurrence_id.isoformat()
    return ""


def _ics_event_key(meeting):
    """Return the lifecycle key for an ICS event.

    A recurring series deliberately shares its UID across occurrences. When a
    RECURRENCE-ID is present it is therefore part of the key; ordinary events
    retain the UID-only identity used by calendar producers.
    """

    return meeting.uid, recurrence_identity(meeting.recurrence_id)


def _ics_should_replace(current, candidate):
    return lifecycle_should_replace(current, candidate)


def ics_master_should_replace(current, candidate):
    """Choose a series master by SEQUENCE and then source freshness."""

    return lifecycle_should_replace(current, candidate)


def _parse_ics_properties(lines):
    properties = {}
    for line in lines:
        parsed = _parse_ics_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        properties.setdefault(name, []).append((params, value))
    return properties


def _parse_ics_event(
    lines,
    record,
    calendar_method="",
    properties=None,
    timezone_definitions=None,
):
    properties = properties if properties is not None else _parse_ics_properties(lines)

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
    start_at, timezone_name = _parse_ics_datetime(
        start_value,
        start_params,
        timezone_definitions,
    )
    recurrence_params, recurrence_value = _ics_first(properties, "RECURRENCE-ID")
    recurrence_id, recurrence_timezone_name = _parse_ics_datetime(
        recurrence_value,
        recurrence_params,
        timezone_definitions,
    )
    if recurrence_id is not None and not timezone_name:
        timezone_name = recurrence_timezone_name
    if start_at is None and normalized_status == "CANCELLED":
        # Calendar cancellations commonly omit DTSTART and identify the
        # cancelled occurrence solely with RECURRENCE-ID.
        start_at = recurrence_id or record.get("received_date")
        if not isinstance(start_at, (date, datetime)):
            start_at = parse_received_date(record.get("date", ""))
    if start_at is None:
        return None

    end_params, end_value = _ics_first(properties, "DTEND")
    end_at, end_timezone_name = _parse_ics_datetime(
        end_value,
        end_params,
        timezone_definitions,
    )
    timezone_name = timezone_name or end_timezone_name
    _, duration_value = _ics_first(properties, "DURATION")
    duration = _parse_duration(duration_value)
    if end_at is None and duration is not None:
        if isinstance(start_at, datetime):
            end_at = start_at + duration
        elif isinstance(start_at, date):
            if duration.seconds == 0 and duration.microseconds == 0:
                end_at = start_at + timedelta(days=duration.days)
            else:
                end_at = datetime.combine(start_at, dt_time.min) + duration

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
        thread_id=str(record.get("thread_id") or ""),
        start_at=start_at,
        end_at=end_at,
        timezone=timezone_name,
        location=location,
        join_url=_extract_join_url(online_values),
        status=normalized_status,
        sequence=sequence,
        source_message_id=source_message_id,
        confidence=1.0,
        source_received_at=record_source_received_at(record),
        recurrence_id=recurrence_id,
    )


def _rule_start(start_at):
    if isinstance(start_at, datetime):
        return start_at, False
    return datetime.combine(start_at, dt_time.min), True


def _recurrence_datetime(
    value,
    params,
    rule_start,
    all_day,
    timezone_definitions=None,
):
    parsed, _ = _parse_ics_datetime(value, params, timezone_definitions)
    if parsed is None:
        return None
    if all_day:
        if isinstance(parsed, datetime):
            if parsed.time() != dt_time.min:
                return None
            return parsed.date()
        return parsed
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return None
    if rule_start.tzinfo is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=rule_start.tzinfo)
        return parsed.astimezone(rule_start.tzinfo)
    if parsed.tzinfo is not None:
        return parsed.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return parsed


def _recurrence_bound(value, rule_start, all_day, is_end):
    if value is None:
        return None
    if all_day:
        bound_date = value.date() if isinstance(value, datetime) else value
        return datetime.combine(
            bound_date,
            dt_time.max if is_end else dt_time.min,
        )
    if isinstance(value, datetime):
        bound = value
    else:
        bound = datetime.combine(
            value,
            dt_time.max if is_end else dt_time.min,
        )
    if rule_start.tzinfo is not None:
        if bound.tzinfo is None:
            local_timezone = (
                ZoneInfo(LOCAL_TIMEZONE_NAME)
                if ZoneInfo is not None
                else rule_start.tzinfo
            )
            bound = bound.replace(tzinfo=local_timezone)
        return bound.astimezone(rule_start.tzinfo)
    if bound.tzinfo is not None:
        return bound.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return bound


def _normalize_rrule_until(rule_text, rule_start):
    parts = rule_text.split(";")
    normalized_parts = []
    until_limit = None
    for part in parts:
        key, separator, value = part.partition("=")
        if not separator or key.upper() != "UNTIL":
            normalized_parts.append(part)
            continue
        until, _ = _parse_ics_datetime(value, {})
        if isinstance(until, date) and not isinstance(until, datetime):
            until_limit = datetime.combine(until, dt_time.max)
            if rule_start.tzinfo is not None:
                until_limit = until_limit.replace(tzinfo=rule_start.tzinfo)
        else:
            if isinstance(until, datetime) and rule_start.tzinfo is not None:
                if until.tzinfo is None:
                    until_limit = until.replace(tzinfo=rule_start.tzinfo)
                else:
                    until_limit = until.astimezone(rule_start.tzinfo)
            elif isinstance(until, datetime) and until.tzinfo is not None:
                until_limit = until.astimezone(dt_timezone.utc).replace(tzinfo=None)
            else:
                until_limit = until
        # The dateutil recurrence is generated in DTSTART's timezone. Apply
        # UNTIL as an aware local cutoff ourselves so UTC UNTIL values retain
        # their instant while local wall-clock times remain stable over DST.
    return ";".join(normalized_parts), until_limit


def expand_ics_series(meeting, properties, range_start, range_end):
    if meeting.start_at is None:
        return [meeting]
    rule_start, all_day = _rule_start(meeting.start_at)
    rule_values = [value for _, value in properties.get("RRULE", [])]
    date_values = properties.get("RDATE", [])
    if not rule_values and not date_values:
        return [meeting]
    timezone_definitions = properties.get(TIMEZONE_DEFINITIONS_PROPERTY, {})

    lower_bound = _recurrence_bound(range_start, rule_start, all_day, False)
    upper_bound = _recurrence_bound(range_end, rule_start, all_day, True)
    if lower_bound is None or upper_bound is None:
        return [meeting]
    candidate_occurrences = set()
    if lower_bound <= rule_start <= upper_bound:
        candidate_occurrences.add(rule_start)
    for rule_text in rule_values:
        normalized_rule, until_limit = _normalize_rrule_until(rule_text, rule_start)
        try:
            rule = rrulestr(normalized_rule, dtstart=rule_start)
        except (TypeError, ValueError, OverflowError):
            continue
        for occurrence in rule.xafter(
            lower_bound,
            count=MAX_RECURRENCES_PER_SERIES,
            inc=True,
        ):
            if occurrence > upper_bound or (
                until_limit is not None and occurrence > until_limit
            ):
                break
            candidate_occurrences.add(occurrence)

    for params, values in date_values:
        for value in values.split(","):
            parsed = _recurrence_datetime(
                value,
                params,
                rule_start,
                all_day,
                timezone_definitions,
            )
            if parsed is None:
                continue
            if all_day:
                parsed = datetime.combine(parsed, dt_time.min)
            candidate_occurrences.add(parsed)

    excluded = set()
    for params, values in properties.get("EXDATE", []):
        for value in values.split(","):
            parsed = _recurrence_datetime(
                value,
                params,
                rule_start,
                all_day,
                timezone_definitions,
            )
            if parsed is None:
                continue
            if all_day:
                parsed = datetime.combine(parsed, dt_time.min)
            excluded.add(parsed)
    duration = None
    if isinstance(meeting.start_at, datetime) and isinstance(meeting.end_at, datetime):
        duration = meeting.end_at - meeting.start_at
    elif isinstance(meeting.start_at, date) and isinstance(meeting.end_at, date):
        duration = datetime.combine(meeting.end_at, dt_time.min) - datetime.combine(
            meeting.start_at,
            dt_time.min,
        )

    occurrences = []
    for occurrence in sorted(candidate_occurrences - excluded):
        if occurrence < lower_bound or occurrence > upper_bound:
            continue
        if len(occurrences) >= MAX_RECURRENCES_PER_SERIES:
            break
        occurrence_start = occurrence.date() if all_day else occurrence
        occurrence_end = None
        if duration is not None:
            if all_day:
                occurrence_end = occurrence_start + duration
            else:
                occurrence_end = occurrence + duration
        occurrences.append(
            replace(
                meeting,
                start_at=occurrence_start,
                end_at=occurrence_end,
                recurrence_id=occurrence_start,
            )
        )
    return occurrences


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


def parse_ics_event_definitions(record):
    """Return parsed (master or override, properties) definitions for one mail.

    ``None`` means the record has no calendar payload; an empty list means it
    had a calendar payload but no usable VEVENT definitions.
    """

    detected, payloads = _ics_payloads_from_record(record)
    if not detected:
        return None

    definitions = []
    for payload in payloads:
        timezone_definitions = _ics_timezone_definitions(payload)
        _, blocks = _ics_event_blocks(payload)
        for calendar_method, block in blocks:
            properties = _parse_ics_properties(block)
            meeting = _parse_ics_event(
                block,
                record,
                calendar_method,
                properties,
                timezone_definitions,
            )
            if meeting is not None:
                properties[TIMEZONE_DEFINITIONS_PROPERTY] = timezone_definitions
                definitions.append((meeting, properties))
    return definitions


def parse_ics_meetings(record, range_start=None, range_end=None):
    """Return parsed ICS meetings, expanding recurring series in the range."""
    definitions = parse_ics_event_definitions(record)
    if definitions is None:
        return None

    if range_start is None:
        range_start = local_now().date()
    range_start_date = range_start.date() if isinstance(range_start, datetime) else range_start
    if range_end is None:
        range_end = range_start_date + timedelta(days=DEFAULT_RECURRENCE_RANGE_DAYS)

    masters = {}
    overrides = {}
    for meeting, properties in definitions:
        destination = overrides if meeting.recurrence_id is not None else masters
        key = _ics_event_key(meeting)
        existing = destination.get(key)
        if existing is None:
            should_replace = True
        elif destination is masters:
            should_replace = ics_master_should_replace(existing[0], meeting)
        else:
            should_replace = _ics_should_replace(existing[0], meeting)
        if should_replace:
            destination[key] = (meeting, properties)

    meetings_by_key = {}
    for meeting, properties in masters.values():
        if meeting.status == "CANCELLED":
            meetings_by_key[_ics_event_key(meeting)] = meeting
            continue
        recurring = expand_ics_series(meeting, properties, range_start, range_end)
        for occurrence in recurring:
            meetings_by_key[_ics_event_key(occurrence)] = occurrence

    master_by_uid = {meeting.uid: meeting for meeting, _ in masters.values()}
    for meeting, _ in overrides.values():
        key = _ics_event_key(meeting)
        master = master_by_uid.get(meeting.uid)
        if master is None or meeting.sequence >= master.sequence:
            meetings_by_key[key] = meeting
    return list(meetings_by_key.values())
