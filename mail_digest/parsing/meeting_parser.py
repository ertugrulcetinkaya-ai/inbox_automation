"""Semantic meeting classification and canonical record conversion."""

from __future__ import annotations

import re
from datetime import date, datetime, time as datetime_time

from ..config import (
    CALENDAR_MARKERS,
    LOCAL_TIMEZONE_NAME,
    MEETING_KEYWORDS,
    SEMANTIC_CANCELLED_RE,
    SEMANTIC_RESCHEDULED_RE,
    SEMANTIC_TENTATIVE_RE,
)
from ..models import Meeting
from .dates import _date_hits, parse_received_date
from .ics import parse_ics_meetings
from .times import _time_for_date

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ provides zoneinfo
    ZoneInfo = None


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
