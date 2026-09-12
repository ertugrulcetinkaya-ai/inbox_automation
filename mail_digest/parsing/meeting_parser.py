"""Semantic meeting classification and canonical record conversion."""

from __future__ import annotations

import re
from datetime import date, datetime, time as datetime_time

from ..config import (
    CALENDAR_MARKERS,
    CALENDAR_SUBJECT_PREFIXES,
    LOCAL_TIMEZONE_NAME,
    MEETING_KEYWORDS,
    SEMANTIC_CANCELLED_RE,
    SEMANTIC_RESCHEDULED_RE,
    SEMANTIC_TENTATIVE_RE,
)
from ..models import Meeting, MeetingOccurrence
from .dates import _date_hits, parse_received_date
from .ics import parse_ics_meetings
from .times import _time_for_date, _time_hits
from ..utils import record_source_received_at, strip_quoted_reply

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ provides zoneinfo
    ZoneInfo = None


SEMANTIC_CONTEXT_RADIUS = 160
EXPLICIT_MEETING_CONTEXT_RE = re.compile(
    r"(?:\b(?:"
    r"toplant[ıi]\w*|meeting\w*|appointment\w*|randevu\w*|"
    r"görüş\w*|gorus\w*|etkinlik\w*|event\w*|conference\w*|"
    r"konferans\w*|seminar\w*|webinar\w*|interview\w*|"
    r"mülakat\w*|mulakat\w*|invitation\w*|invite\w*|davetiye\w*|"
    r"zoom\w*|webex\w*"
    r")\b|google\s+meet\b|microsoft\s+teams\b|join\s+meeting\b)",
    re.IGNORECASE,
)


def _meeting_display_start(meeting):
    start_at = meeting.start_at
    if isinstance(start_at, datetime) and start_at.tzinfo is not None and ZoneInfo is not None:
        try:
            return start_at.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME))
        except (KeyError, OverflowError, ValueError):
            return start_at
    return start_at


def _meeting_display_date(meeting):
    start_at = _meeting_display_start(meeting)
    return start_at.date() if isinstance(start_at, datetime) else start_at


def _meeting_to_digest_record(
    meeting,
    position=0,
    date_override=None,
) -> MeetingOccurrence:
    start_at = _meeting_display_start(meeting)
    end_at = meeting.end_at
    if isinstance(end_at, datetime) and end_at.tzinfo is not None and ZoneInfo is not None:
        try:
            end_at = end_at.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME))
        except (KeyError, OverflowError, ValueError):
            end_at = meeting.end_at

    if isinstance(start_at, datetime):
        label = start_at.strftime("%H:%M")
        sort_minutes = start_at.hour * 60 + start_at.minute
        end_sort_minutes = None
        if isinstance(end_at, datetime) and end_at.date() == start_at.date():
            label = f"{label}–{end_at.strftime('%H:%M')}"
            end_sort_minutes = end_at.hour * 60 + end_at.minute
    else:
        label = "Tüm gün"
        sort_minutes = 24 * 60
        end_sort_minutes = None

    return {
        "subject": meeting.title,
        "sender": meeting.organizer or "Bilinmeyen gönderen",
        "date": _meeting_display_date(meeting) or date_override,
        "time": label,
        "sort_minutes": sort_minutes,
        "end_sort_minutes": end_sort_minutes,
        "uid": meeting.uid,
        "organizer": meeting.organizer,
        "location": meeting.location,
        "join_url": meeting.join_url,
        "status": meeting.status,
        "sequence": meeting.sequence,
        "source_message_id": meeting.source_message_id,
        "confidence": meeting.confidence,
        "source_received_at": meeting.source_received_at,
        "supersedes_start_at": meeting.supersedes_start_at,
        "recurrence_id": meeting.recurrence_id,
        "_position": position,
    }


def _is_meeting_message(subject, content):
    haystack = f"{subject}\n{content}".casefold()
    if any(keyword in haystack for keyword in MEETING_KEYWORDS + CALENDAR_MARKERS):
        return True
    if EXPLICIT_MEETING_CONTEXT_RE.search(haystack):
        return True
    # Subject-specific calendar invitation prefixes (e.g. Turkish "Davet:") are
    # matched only on the normalized subject, never across the body.
    normalized_subject = (subject or "").casefold()
    return any(normalized_subject.startswith(prefix) for prefix in CALENDAR_SUBJECT_PREFIXES)


def _has_strong_subject_signal(subject):
    normalized_subject = (subject or "").casefold()
    return bool(EXPLICIT_MEETING_CONTEXT_RE.search(normalized_subject)) or any(
        normalized_subject.startswith(prefix) for prefix in CALENDAR_SUBJECT_PREFIXES
    )


def _distance_to_date(item, date_hit):
    if item["end"] <= date_hit["start"]:
        return date_hit["start"] - item["end"]
    if item["start"] >= date_hit["end"]:
        return item["start"] - date_hit["end"]
    return 0


def _semantic_context_for_date(text, date_hit):
    start = max(0, date_hit["start"] - SEMANTIC_CONTEXT_RADIUS)
    end = min(len(text), date_hit["end"] + SEMANTIC_CONTEXT_RADIUS)
    return text[start:end]


def _has_time_near_date(text, date_hit, time_hits=None):
    if time_hits is None:
        time_hits = _time_hits(text)
    return any(
        _distance_to_date(time_hit, date_hit) <= SEMANTIC_CONTEXT_RADIUS
        for time_hit in time_hits
    )


def _passes_detailed_semantic_review(subject, text, date_hit, time_hits=None):
    """Confirm a non-ICS date is really tied to a meeting context.

    A strong meeting signal in the subject can support an all-day semantic
    event. Body-only candidates are deliberately stricter: the date must have
    both an explicit meeting phrase and a nearby time. This keeps report,
    signature and quoted-thread dates out of the digest without hiding
    low-confidence meetings that do pass the second review.
    """

    if _has_strong_subject_signal(subject):
        return True

    context = _semantic_context_for_date(text, date_hit)
    normalized_context = context.casefold()
    has_meeting_context = bool(EXPLICIT_MEETING_CONTEXT_RE.search(context)) or any(
        marker in normalized_context for marker in CALENDAR_MARKERS
    )
    return has_meeting_context and _has_time_near_date(text, date_hit, time_hits)


def _received_date_for_record(record, fallback_date):
    source_received_at = record_source_received_at(record)
    if source_received_at is not None:
        return source_received_at.date()
    if "received_date" in record:
        received_date = record.get("received_date")
        if not isinstance(received_date, (date, datetime)):
            received_date = parse_received_date(received_date)
    elif "date" in record:
        received_date = parse_received_date(record.get("date"))
    else:
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
        return [date_hits[-1]]
    return date_hits


def _semantic_meeting_from_date_hit(
    record,
    subject,
    text,
    date_hit,
    status,
    supersedes_start_at=None,
):
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
        source_received_at=record_source_received_at(record),
        supersedes_start_at=supersedes_start_at,
    )


def _semantic_superseded_start(text, date_hits, status, selected_hit, time_hits=None):
    """Return the old occurrence mentioned by a semantic reschedule message."""

    if status != "RESCHEDULED" or len(date_hits) < 2 or selected_hit is not date_hits[-1]:
        return None
    old_hit = date_hits[0]
    next_date_hit = date_hits[1]
    time_hits = time_hits if time_hits is not None else _time_hits(text)
    old_time = next(
        (
            item
            for item in time_hits
            if _distance_to_date(item, old_hit) <= SEMANTIC_CONTEXT_RADIUS
            and item["end"] <= next_date_hit["start"]
        ),
        None,
    )
    if old_time is None:
        # A common reschedule wording gives a time only for the new date. Keep
        # the old date as a wildcard occurrence so the prior timed invitation
        # can still be superseded without guessing its old time.
        return old_hit["date"]
    return datetime.combine(
        old_hit["date"],
        datetime_time(old_time["minutes"] // 60, old_time["minutes"] % 60),
    )


def extract_meetings(
    record,
    start_date,
    end_date=None,
    include_cancelled=False,
    include_lifecycle_outside_range=False,
):
    subject = record.get("subject", "")
    raw_content = record.get("content", record.get("snippet", ""))
    content = strip_quoted_reply(raw_content)
    received_date = _received_date_for_record(record, start_date)

    # Calendar data is authoritative and is parsed before subject filtering.
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
            if not include_lifecycle_outside_range:
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
    all_date_hits = _date_hits(text, start_date, relative_date=received_date)
    time_hits = _time_hits(text)
    date_hits = [
        hit
        for hit in all_date_hits
        if _passes_detailed_semantic_review(subject, text, hit, time_hits)
    ]
    lifecycle_date_hits = _semantic_date_hits_for_status(text, date_hits, status)
    if include_lifecycle_outside_range and status == "RESCHEDULED":
        matching_dates = lifecycle_date_hits
    else:
        matching_dates = [
            hit
            for hit in lifecycle_date_hits
            if hit["date"] >= start_date
            and (end_date is None or hit["date"] <= end_date)
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
                        source_received_at=record_source_received_at(record),
                    ),
                    date_override=received_date or start_date,
                )
            ]
    return [
        _meeting_to_digest_record(
            _semantic_meeting_from_date_hit(
                record,
                subject,
                text,
                date_hit,
                status,
                supersedes_start_at=_semantic_superseded_start(
                    text,
                    date_hits,
                    status,
                    date_hit,
                    time_hits,
                ),
            ),
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
