"""Canonical data models."""

from dataclasses import dataclass


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
