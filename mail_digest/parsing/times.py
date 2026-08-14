"""Time extraction and date/time association."""

import re

from .dates import _numeric_dot_token_kind_at


TIME_WITH_COLON_RE = re.compile(
    r"(?<![\d.])(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
TIME_WITH_DOT_RE = re.compile(
    r"(?<![\d.])(?P<hour>\d{1,2})\.(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
TIME_WITH_AMPM_RE = re.compile(
    r"(?<!\w)(?P<hour>1[0-2]|[1-9])(?:[:.](?P<minute>\d{2}))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)


def _time_hits(text):
    matches = []
    for pattern in (TIME_WITH_COLON_RE, TIME_WITH_DOT_RE, TIME_WITH_AMPM_RE):
        for match in pattern.finditer(text):
            if _numeric_dot_token_kind_at(text, match.start()) == "date":
                continue
            matches.append(match)

    unique = {}
    for match in matches:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        ampm = (match.group("ampm") or "").replace(".", "").lower()

        if ampm:
            if hour == 12:
                hour = 0
            if ampm == "pm":
                hour += 12
        if hour > 23 or minute > 59:
            continue

        if match.start() > 0 and text[match.start() - 1].isdigit():
            continue
        if match.end() < len(text) and text[match.end()] == ".":
            following = text[match.end() + 1:match.end() + 5]
            if following.isdigit() and len(following) == 4:
                continue

        unique[(match.start(), match.end())] = {
            "start": match.start(),
            "end": match.end(),
            "minutes": hour * 60 + minute,
            "label": f"{hour:02d}:{minute:02d}",
        }
    return sorted(unique.values(), key=lambda item: item["start"])


def _time_for_date(text, date_hit):
    window_start = max(0, date_hit["start"] - 260)
    window_end = min(len(text), date_hit["end"] + 260)
    nearby = [
        item
        for item in _time_hits(text[window_start:window_end])
        if abs((item["start"] + window_start) - date_hit["start"]) <= 260
    ]
    if not nearby:
        return None

    for item in nearby:
        item["start"] += window_start
        item["end"] += window_start

    after_date = [item for item in nearby if item["start"] >= date_hit["end"]]
    selected = after_date[:2] if after_date else nearby[-2:]
    if not selected:
        return None

    label = selected[0]["label"]
    end_minutes = None
    if len(selected) > 1:
        between = text[selected[0]["end"]:selected[1]["start"]].casefold()
        if re.search(r"[-–—]|\b(to|until|ile|kadar)\b", between):
            label = f"{label}–{selected[1]['label']}"
            end_minutes = selected[1]["minutes"]
    return {"label": label, "minutes": selected[0]["minutes"], "end_minutes": end_minutes}
