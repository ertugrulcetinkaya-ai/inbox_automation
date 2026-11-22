"""Shared event lifecycle ordering for ICS and meeting aggregation."""

from __future__ import annotations

from collections.abc import Mapping

from ..utils import record_source_received_at


STATUS_PRIORITY = {
    "CONFIRMED": 0,
    "TENTATIVE": 1,
    "RESCHEDULED": 2,
    "CANCELLED": 3,
}


def _field(event, name, default=None):
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _sequence(event):
    try:
        return int(_field(event, "sequence", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _received_at(event):
    return record_source_received_at(
        {"source_received_at": _field(event, "source_received_at")}
    )


def lifecycle_should_replace(current, candidate):
    """Whether *candidate* is the newer authoritative lifecycle event.

    Sequence orders explicit calendar revisions. At equal sequence, a
    cancellation always wins; otherwise source freshness wins before status
    preference, with message ID providing a stable final tie-breaker.
    """

    current_sequence = _sequence(current)
    candidate_sequence = _sequence(candidate)
    if candidate_sequence != current_sequence:
        return candidate_sequence > current_sequence

    current_status = str(_field(current, "status", "CONFIRMED")).upper()
    candidate_status = str(_field(candidate, "status", "CONFIRMED")).upper()
    if (candidate_status == "CANCELLED") != (current_status == "CANCELLED"):
        return candidate_status == "CANCELLED"

    current_received = _received_at(current)
    candidate_received = _received_at(candidate)
    if candidate_received != current_received:
        if current_received is None:
            return candidate_received is not None
        if candidate_received is None:
            return False
        return candidate_received > current_received

    candidate_priority = STATUS_PRIORITY.get(candidate_status, 0)
    current_priority = STATUS_PRIORITY.get(current_status, 0)
    if candidate_priority != current_priority:
        return candidate_priority > current_priority

    return str(_field(candidate, "source_message_id", "")) > str(
        _field(current, "source_message_id", "")
    )
