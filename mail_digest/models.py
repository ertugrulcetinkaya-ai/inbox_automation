"""Canonical data models."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, NotRequired, TypedDict


MeetingDate = date | datetime
MeetingStatus = Literal["CONFIRMED", "CANCELLED", "RESCHEDULED", "TENTATIVE"]


class MeetingOccurrence(TypedDict):
    """Canonical mapping returned by meeting extraction.

    The fields needed by every renderer are required. Source metadata and
    optional lifecycle/display details remain optional so the established
    mapping-based service API can stay backwards compatible.
    """

    subject: str
    sender: str
    date: date | None
    time: str
    sort_minutes: int
    end_sort_minutes: NotRequired[int | None]
    uid: NotRequired[str]
    organizer: NotRequired[str]
    location: NotRequired[str]
    join_url: NotRequired[str]
    status: NotRequired[MeetingStatus]
    sequence: NotRequired[int]
    source_message_id: NotRequired[str]
    confidence: NotRequired[float]
    source_received_at: NotRequired[datetime | None]
    supersedes_start_at: NotRequired[MeetingDate | None]
    recurrence_id: NotRequired[MeetingDate | None]
    thread_id: NotRequired[str]
    schedule_warnings: NotRequired[list[str]]
    _position: NotRequired[int]


@dataclass(frozen=True)
class Meeting:
    """Canonical meeting model shared by ICS and semantic fallback parsing."""

    uid: str = ""
    title: str = ""
    organizer: str = ""
    # Gmail thread identity is a useful semantic lifecycle fallback when an
    # invitation and its cancellation use different display names/subjects.
    thread_id: str = ""
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
