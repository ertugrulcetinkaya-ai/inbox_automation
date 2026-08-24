"""Meeting aggregation, deduplication, and digest rendering."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from ..config import ATTENTION_CONFIDENCE_THRESHOLD, local_now
from ..parsing.meeting_parser import extract_meetings


TR_OUTPUT_MONTHS = (
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)
TR_OUTPUT_WEEKDAYS = (
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
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
                include_lifecycle_outside_range=True,
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


def format_digest(records, target_date=None):
    target_date = target_date or local_now().date()
    meetings = _collect_meetings(records, target_date, target_date)

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


def format_upcoming_digest(records, start_date=None):
    start_date = start_date or local_now().date()
    meetings = _collect_meetings(records, start_date)

    if not meetings:
        return "📅 Bugün ve sonraki toplantılar\nBugün veya sonrasında toplantı yok."

    lines = ["📅 Bugün ve sonraki toplantılar", ""]
    regular = [meeting for meeting in meetings if not _needs_attention(meeting)]
    _render_grouped_meetings(lines, regular)
    _render_attention_section(lines, meetings, include_dates=True)
    return "\n".join(lines).rstrip()


def _with_schedule_warnings(meetings, short_break_minutes=15):
    annotated = [{**meeting, "schedule_warnings": []} for meeting in meetings]
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


def format_weekly_digest(records, start_date=None, days=7):
    start_date = start_date or local_now().date()
    end_date = start_date + timedelta(days=days - 1)
    meetings = _with_schedule_warnings(_collect_meetings(records, start_date, end_date))

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


def format_attention_digest(records, start_date=None):
    start_date = start_date or local_now().date()
    attention = [
        meeting
        for meeting in _collect_meetings(records, start_date)
        if _needs_attention(meeting)
    ]
    if not attention:
        return "⚠️ Dikkat gerektirenler\nBugün veya sonrasında dikkat gerektiren toplantı yok."

    lines = ["⚠️ Dikkat gerektirenler", ""]
    _render_grouped_meetings(lines, attention)
    return "\n".join(lines).rstrip()


def due_reminder_meetings(records, now=None, lead_minutes=15, window_minutes=5):
    now = now or local_now()
    due_start = now + timedelta(minutes=max(0, lead_minutes - window_minutes))
    due_end = now + timedelta(minutes=lead_minutes)
    meetings = _collect_meetings(records, due_start.date(), due_end.date())
    due = []
    for meeting in meetings:
        if meeting["sort_minutes"] >= 24 * 60:
            continue
        starts_at = datetime.combine(
            meeting["date"],
            time(meeting["sort_minutes"] // 60, meeting["sort_minutes"] % 60),
        )
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
