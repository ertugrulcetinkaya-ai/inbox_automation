"""Canonical data models."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, TypedDict


MeetingDate = date | datetime
MeetingStatus = Literal["CONFIRMED", "CANCELLED", "RESCHEDULED", "TENTATIVE"]


class MeetingOccurrence(TypedDict, total=False):
    """Typed dictionary used by renderers and reminder selection.

    The service layer intentionally keeps its established mapping-based API;
    this type makes that boundary explicit without forcing a broad runtime
    refactor of the formatting code.
    """

    subject: str
    sender: str
    date: date | None
    time: str
    sort_minutes: int
    end_sort_minutes: int | None
    uid: str
    organizer: str
    location: str
    join_url: str
    status: MeetingStatus
    sequence: int
    source_message_id: str
    confidence: float
    source_received_at: datetime | None
    supersedes_start_at: MeetingDate | None
    recurrence_id: MeetingDate | None
    schedule_warnings: list[str]
    _position: int


@dataclass(frozen=True)
class Meeting:
    """Canonical meeting model shared by ICS and semantic fallback parsing."""

    uid: str = ""
    title: str = ""
    organizer: str = ""
    start_at: MeetingDate | None = None
    end_at: MeetingDate | None = None
    timezone: str = ""
    location: str = ""
    join_url: str = ""
    status: MeetingStatus = "CONFIRMED"
    sequence: int = 0
    source_message_id: str = ""
    confidence: float = 0.0
    # Gmail's internalDate (or the closest source timestamp) is used to break
    # same-UID/same-SEQUENCE ties deterministically. It is deliberately kept
    # out of the user-facing digest.
    source_received_at: datetime | None = None
    # Semantic reschedule messages often mention the old and new occurrence.
    # The new occurrence is the meeting's start_at; this field lets aggregation
    # suppress only the old occurrence without using a subject-global rule.
    supersedes_start_at: MeetingDate | None = None
    # A recurring ICS series shares a UID across occurrences. RECURRENCE-ID is
    # therefore part of the lifecycle identity when present.
    recurrence_id: MeetingDate | None = None
