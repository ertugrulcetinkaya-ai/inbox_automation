"""Meeting aggregation, deduplication, and digest rendering."""

from __future__ import annotations

import re
from datetime import datetime

from ..parsing.meeting_parser import extract_meetings


TR_OUTPUT_MONTHS = (
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


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


def _render_meeting_lines(lines, meeting):
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


def format_digest(records, target_date=None):
    target_date = target_date or datetime.now().date()
    meetings = _collect_meetings(records, target_date, target_date)

    date_label = format_date(target_date)
    if not meetings:
        return f"📅 {date_label}\nBugün toplantı yok."

    lines = [f"📅 Bugünkü toplantılar — {date_label}", ""]
    for meeting in meetings:
        _render_meeting_lines(lines, meeting)
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
        _render_meeting_lines(lines, meeting)
    return "\n".join(lines).rstrip()
