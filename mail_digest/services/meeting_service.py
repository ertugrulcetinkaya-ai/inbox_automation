"""Meeting aggregation, deduplication, and digest rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.utils import parseaddr
from typing import cast
from zoneinfo import ZoneInfo

from ..config import ATTENTION_CONFIDENCE_THRESHOLD, LOCAL_TIMEZONE_NAME, local_now
from ..models import MeetingOccurrence
from ..parsing.meeting_parser import extract_meetings
from ..utils import record_source_received_at


TR_OUTPUT_MONTHS = (
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)
TR_OUTPUT_WEEKDAYS = (
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
)


@dataclass(frozen=True)
class DigestResult:
    """One parsed digest result shared by rendering and CLI telemetry."""

    message: str
    meetings: list[MeetingOccurrence]
    count: int


def format_date(target_date):
    return f"{target_date.day} {TR_OUTPUT_MONTHS[target_date.month]} {target_date.year}"


def _status_label(status):
    return {
        "RESCHEDULED": "Ertelendi",
        "TENTATIVE": "Kesinleşmedi",
    }.get(status, "")


def _collect_meetings(records, start_date, end_date=None) -> list[MeetingOccurrence]:
    meetings = []
    for record in records:
        meetings.extend(
            extract_meetings(
                record,
                start_date,
                end_date,
                include_cancelled=True,
                include_lifecycle_outside_range=True,
            )
        )

    status_priority = {
        "CONFIRMED": 0,
        "TENTATIVE": 1,
        "RESCHEDULED": 2,
        "CANCELLED": 3,
    }

    def text_key(value):
        return re.sub(r"\W+", " ", str(value or "").casefold()).strip()

    lifecycle_subject_suffixes = (
        "iptal edildi",
        "iptal edildiği",
        "iptali",
        "iptal",
        "cancellation",
        "cancelled",
        "canceled",
        "cancel",
        "rescheduled",
        "ertelendi",
        "ertelenen",
    )

    def subject_key(meeting):
        normalized = text_key(meeting.get("subject", ""))
        # Calendar clients frequently change only the subject suffix when
        # sending a cancellation/update (for example, "Satış toplantısı
        # iptali"). Strip only known lifecycle suffixes; ordinary subject
        # words remain part of the semantic identity.
        for suffix in lifecycle_subject_suffixes:
            if normalized.endswith(f" {suffix}"):
                normalized = normalized[: -(len(suffix) + 1)].rstrip()
                break
        return normalized

    def organizer_key(meeting):
        raw_value = meeting.get("organizer") or meeting.get("sender", "")
        _, address = parseaddr(str(raw_value))
        return address.casefold().strip() if address else text_key(raw_value)

    def thread_key(meeting):
        return text_key(meeting.get("thread_id", ""))

    def occurrence_parts(meeting, start_at=None):
        if isinstance(start_at, datetime):
            return start_at.date(), start_at.hour * 60 + start_at.minute
        if isinstance(start_at, date):
            return start_at, None
        meeting_date = meeting.get("date")
        sort_minutes = meeting.get("sort_minutes")
        if sort_minutes is not None and sort_minutes >= 24 * 60:
            sort_minutes = None
        return meeting_date, sort_minutes

    def semantic_identity(meeting, start_at=None, allow_date_only=False):
        meeting_date, sort_minutes = occurrence_parts(meeting, start_at)
        if meeting_date is None or sort_minutes is None:
            if meeting_date is None:
                return None
        if (
            allow_date_only
            and isinstance(start_at, date)
            and not isinstance(start_at, datetime)
        ):
            sort_minutes = None
        return subject_key(meeting), organizer_key(meeting), meeting_date, sort_minutes

    def lifecycle_matches(update, candidate, update_start_at=None):
        """Match one lifecycle update to one semantic occurrence.

        A Gmail thread is authoritative when both sides have one. When one
        side lacks a thread (Apple Mail or older cache records), fall back to
        canonical sender email plus normalized subject family. A date-only
        lifecycle update deliberately matches any time on that date, while a
        timed update remains exact.
        """

        update_date, update_minutes = occurrence_parts(update, update_start_at)
        candidate_date, candidate_minutes = occurrence_parts(candidate)
        if update_date is None or candidate_date != update_date:
            return False
        if update_minutes is not None and candidate_minutes != update_minutes:
            return False

        update_thread = thread_key(update)
        candidate_thread = thread_key(candidate)
        if update_thread and candidate_thread:
            return update_thread == candidate_thread
        return (
            subject_key(update) == subject_key(candidate)
            and organizer_key(update) == organizer_key(candidate)
        )

    def event_key(meeting):
        uid = meeting.get("uid", "")
        recurrence_id = meeting.get("recurrence_id")
        if isinstance(recurrence_id, (date, datetime)):
            recurrence_id = recurrence_id.isoformat()
        return uid, recurrence_id or ""

    def freshness_key(meeting):
        received_at = record_source_received_at(meeting)
        if received_at is None:
            return (0, 0.0)
        return (1, received_at.timestamp())

    def should_replace(current, candidate):
        if candidate.get("sequence", 0) != current.get("sequence", 0):
            return candidate.get("sequence", 0) > current.get("sequence", 0)
        candidate_priority = status_priority.get(candidate.get("status"), 0)
        current_priority = status_priority.get(current.get("status"), 0)
        if candidate_priority != current_priority:
            return candidate_priority > current_priority
        candidate_freshness = freshness_key(candidate)
        current_freshness = freshness_key(current)
        if candidate_freshness != current_freshness:
            return candidate_freshness > current_freshness
        # Equal timestamps are unusual, but a stable final tie-breaker keeps
        # aggregation independent of Gmail's record ordering.
        return candidate.get("source_message_id", "") > current.get("source_message_id", "")

    latest_by_event = {}
    without_uid = []
    for meeting in meetings:
        uid = meeting.get("uid", "")
        if not uid:
            without_uid.append(meeting)
            continue
        key = event_key(meeting)
        current = latest_by_event.get(key)
        if current is None or should_replace(current, meeting):
            latest_by_event[key] = meeting

    resolved = list(latest_by_event.values()) + without_uid
    # Semantic mail has no authoritative UID. Correlate lifecycle messages in
    # a narrow order: Gmail thread ID first, then canonical sender email plus
    # subject family, always scoped to the affected occurrence date/time. A
    # date-only cancellation is an intentional wildcard for time on that date;
    # it must still be able to suppress the original timed invitation.
    cancelled_updates = [
        meeting for meeting in without_uid if meeting.get("status") == "CANCELLED"
    ]
    rescheduled_updates = [
        meeting
        for meeting in without_uid
        if meeting.get("status") == "RESCHEDULED"
        and meeting.get("supersedes_start_at") is not None
    ]

    unique_meetings = {}
    for meeting in resolved:
        status = meeting.get("status", "CONFIRMED")
        if status == "CANCELLED":
            continue
        identity = semantic_identity(meeting) if not meeting.get("uid") else None
        lifecycle_suppressed = any(
            lifecycle_matches(cancellation, meeting)
            for cancellation in cancelled_updates
        )
        if not lifecycle_suppressed:
            lifecycle_suppressed = any(
                lifecycle_matches(
                    rescheduled,
                    meeting,
                    rescheduled.get("supersedes_start_at"),
                )
                for rescheduled in rescheduled_updates
            )
        if status in {"CONFIRMED", "TENTATIVE"} and lifecycle_suppressed:
            continue
        if meeting.get("uid"):
            key = ("ics", *event_key(meeting))
        else:
            key = ("semantic", identity)
        current = unique_meetings.get(key)
        if current is None or should_replace(current, meeting):
            unique_meetings[key] = meeting
    in_range = [
        meeting
        for meeting in unique_meetings.values()
        if meeting.get("date") is not None
        and meeting["date"] >= start_date
        and (end_date is None or meeting["date"] <= end_date)
    ]
    return sorted(
        in_range,
        key=lambda item: (item["date"], item["sort_minutes"], item["subject"].casefold()),
    )


def attention_reasons(meeting):
    reasons = []
    status = meeting.get("status", "CONFIRMED")
    if status == "RESCHEDULED":
        reasons.append("ertelendi")
    elif status == "TENTATIVE":
        reasons.append("kesinleşmedi")
    if meeting.get("confidence", 1.0) < ATTENTION_CONFIDENCE_THRESHOLD:
        reasons.append("düşük güvenli ayrıştırma")
    return reasons


def _needs_attention(meeting):
    return bool(attention_reasons(meeting))


def _render_meeting_lines(lines, meeting, remaining_minutes=None):
    subject = meeting["subject"][:100]
    sender = meeting["sender"][:80]
    status_label = _status_label(meeting.get("status", "CONFIRMED"))
    status_suffix = f" [{status_label}]" if status_label else ""
    warning_suffix = ""
    warnings = meeting.get("schedule_warnings", ())
    if warnings:
        warning_suffix = f" ⚠️ {', '.join(warnings)}"
    reminder_suffix = ""
    if remaining_minutes is not None:
        reminder_suffix = f" ({remaining_minutes} dk kaldı)"
    lines.append(
        f"• {meeting['time']} — {subject}{status_suffix}{reminder_suffix}{warning_suffix}"
    )
    lines.append(f"  Gönderen: {sender}")
    if meeting.get("location"):
        lines.append(f"  Yer: {meeting['location'][:160]}")
    if meeting.get("join_url"):
        lines.append(f"  Katılım: {meeting['join_url'][:300]}")
    reasons = attention_reasons(meeting)
    if reasons:
        lines.append(f"  Dikkat: {', '.join(reasons)}")


def _render_grouped_meetings(lines, meetings, include_weekday=False):
    current_date = None
    for meeting in meetings:
        if meeting["date"] != current_date:
            if current_date is not None:
                lines.append("")
            label = format_date(meeting["date"])
            if include_weekday:
                label = f"{TR_OUTPUT_WEEKDAYS[meeting['date'].weekday()]} — {label}"
            lines.append(label)
            current_date = meeting["date"]
        _render_meeting_lines(lines, meeting)


def _render_attention_section(lines, meetings, include_dates=False, include_weekday=False):
    attention = [meeting for meeting in meetings if _needs_attention(meeting)]
    if not attention:
        return
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(("⚠️ Dikkat gerektirenler", ""))
    if include_dates:
        _render_grouped_meetings(lines, attention, include_weekday=include_weekday)
    else:
        for meeting in attention:
            _render_meeting_lines(lines, meeting)
            lines.append("")


def _render_digest(meetings, target_date):
    date_label = format_date(target_date)
    if not meetings:
        return f"📅 {date_label}\nBugün toplantı yok."

    lines = [f"📅 Bugünkü toplantılar — {date_label}", ""]
    regular = [meeting for meeting in meetings if not _needs_attention(meeting)]
    for meeting in regular:
        _render_meeting_lines(lines, meeting)
        lines.append("")
    _render_attention_section(lines, meetings)
    return "\n".join(lines).rstrip()


def format_digest(records, target_date=None):
    target_date = target_date or local_now().date()
    return _render_digest(
        _collect_meetings(records, target_date, target_date),
        target_date,
    )


def _render_upcoming_digest(meetings):
    if not meetings:
        return "📅 Bugün ve sonraki toplantılar\nBugün veya sonrasında toplantı yok."

    lines = ["📅 Bugün ve sonraki toplantılar", ""]
    regular = [meeting for meeting in meetings if not _needs_attention(meeting)]
    _render_grouped_meetings(lines, regular)
    _render_attention_section(lines, meetings, include_dates=True)
    return "\n".join(lines).rstrip()


def format_upcoming_digest(records, start_date=None):
    start_date = start_date or local_now().date()
    return _render_upcoming_digest(_collect_meetings(records, start_date))


def _with_schedule_warnings(
    meetings: list[MeetingOccurrence],
    short_break_minutes=15,
) -> list[MeetingOccurrence]:
    annotated = [
        cast(MeetingOccurrence, {**meeting, "schedule_warnings": []})
        for meeting in meetings
    ]
    by_date = {}
    for meeting in annotated:
        if meeting["sort_minutes"] < 24 * 60:
            by_date.setdefault(meeting["date"], []).append(meeting)

    for day_meetings in by_date.values():
        day_meetings.sort(key=lambda item: item["sort_minutes"])
        for index, current in enumerate(day_meetings):
            current_end = current.get("end_sort_minutes")
            if current_end is None:
                continue
            for candidate in day_meetings[index + 1:]:
                if candidate["sort_minutes"] >= current_end:
                    break
                if "Çakışma" not in current["schedule_warnings"]:
                    current["schedule_warnings"].append("Çakışma")
                if "Çakışma" not in candidate["schedule_warnings"]:
                    candidate["schedule_warnings"].append("Çakışma")

        for previous, current in zip(day_meetings, day_meetings[1:]):
            previous_end = previous.get("end_sort_minutes")
            if previous_end is None:
                continue
            gap = current["sort_minutes"] - previous_end
            if 0 <= gap < short_break_minutes:
                current["schedule_warnings"].append(f"yalnızca {gap} dk ara")
    return annotated


def _render_weekly_digest(meetings, start_date, end_date):
    if not meetings:
        return (
            f"🗓️ Haftalık toplantılar — {format_date(start_date)} / {format_date(end_date)}\n"
            "Bu 7 günlük dönemde toplantı yok."
        )

    lines = [
        f"🗓️ Haftalık toplantılar — {format_date(start_date)} / {format_date(end_date)}",
        "",
    ]
    regular = [meeting for meeting in meetings if not _needs_attention(meeting)]
    _render_grouped_meetings(lines, regular, include_weekday=True)
    _render_attention_section(lines, meetings, include_dates=True, include_weekday=True)
    return "\n".join(lines).rstrip()


def format_weekly_digest(records, start_date=None, days=7):
    start_date = start_date or local_now().date()
    end_date = start_date + timedelta(days=days - 1)
    meetings = _with_schedule_warnings(_collect_meetings(records, start_date, end_date))
    return _render_weekly_digest(meetings, start_date, end_date)


def _render_attention_digest(meetings):
    attention = [
        meeting for meeting in meetings if _needs_attention(meeting)
    ]
    if not attention:
        return "⚠️ Dikkat gerektirenler\nBugün veya sonrasında dikkat gerektiren toplantı yok."

    lines = ["⚠️ Dikkat gerektirenler", ""]
    _render_grouped_meetings(lines, attention)
    return "\n".join(lines).rstrip()


def format_attention_digest(records, start_date=None):
    start_date = start_date or local_now().date()
    return _render_attention_digest(_collect_meetings(records, start_date))


def build_digest_result(records, mode="daily", target_date=None, days=7):
    """Parse records once and return both rendered text and telemetry data."""

    target_date = target_date or local_now().date()
    if mode == "daily":
        meetings = _collect_meetings(records, target_date, target_date)
        message = _render_digest(meetings, target_date)
    elif mode == "upcoming":
        meetings = _collect_meetings(records, target_date)
        message = _render_upcoming_digest(meetings)
    elif mode == "weekly":
        end_date = target_date + timedelta(days=days - 1)
        meetings = _with_schedule_warnings(
            _collect_meetings(records, target_date, end_date)
        )
        message = _render_weekly_digest(meetings, target_date, end_date)
    elif mode == "attention":
        meetings = _collect_meetings(records, target_date)
        message = _render_attention_digest(meetings)
    else:
        raise ValueError(f"Unknown digest mode: {mode}")
    return DigestResult(message=message, meetings=meetings, count=len(meetings))


def due_reminder_meetings(
    records,
    now=None,
    lead_minutes=15,
    window_minutes=5,
    since=None,
):
    now = now or local_now()
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME)).replace(tzinfo=None)
    if since is not None and since.tzinfo is not None:
        since = since.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME)).replace(tzinfo=None)
    # A successful previous scan becomes the lower bound for the next scan.
    # This intentionally overlaps the scheduler window after a lock/contention
    # skip; reminder_key() makes the overlap idempotent.
    due_start = (
        since
        if since is not None
        else now - timedelta(minutes=max(0, window_minutes))
    )
    due_end = now + timedelta(minutes=lead_minutes)
    meetings = _collect_meetings(records, due_start.date(), due_end.date())
    due = []
    for meeting in meetings:
        if meeting["sort_minutes"] >= 24 * 60:
            continue
        if meeting["date"] is None:
            continue
        starts_at = datetime.combine(
            meeting["date"],
            time(meeting["sort_minutes"] // 60, meeting["sort_minutes"] % 60),
        )
        if starts_at <= now:
            continue
        if due_start < starts_at <= due_end:
            due.append(meeting)
    return due


def format_reminder_digest(meetings, now=None):
    now = now or local_now()
    lines = ["⏰ Toplantı hatırlatması", ""]
    for meeting in meetings:
        starts_at = datetime.combine(
            meeting["date"],
            time(meeting["sort_minutes"] // 60, meeting["sort_minutes"] % 60),
        )
        remaining_seconds = max(0, (starts_at - now).total_seconds())
        remaining = int((remaining_seconds + 59) // 60)
        _render_meeting_lines(lines, meeting, remaining_minutes=remaining)
        lines.append("")
    return "\n".join(lines).rstrip()
