"""Shared transport and text-cleaning helpers."""

import re
from datetime import date, datetime, time as datetime_time
from zoneinfo import ZoneInfo

from .config import LOCAL_TIMEZONE_NAME, TRANSPORT_NEWLINE_TOKEN

QUOTED_REPLY_HEADER_RE = re.compile(
    r"^(?:on .+ wrote:|.+ yazdı:|-+original message-+|-+forwarded message-+)$",
    re.IGNORECASE,
)
THREAD_HEADER_RE = re.compile(
    r"^(?P<name>from|gönderen|sent|gönderildi|date|tarih|to|kime|cc|subject|konu):\s*.+$",
    re.IGNORECASE,
)
THREAD_START_HEADERS = {"from", "gönderen"}


def sanitize(text):
    if text is None:
        return ""
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch == " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def restore_transport_newlines(text):
    return (text or "").replace(TRANSPORT_NEWLINE_TOKEN, "\n")


def sanitize_transport_field(text):
    return sanitize(restore_transport_newlines(text))


def sanitize_content(text):
    """Clean Mail content while preserving line structure required by ICS."""
    text = restore_transport_newlines(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\t")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text.strip()


def limit_utf8_bytes(text, maximum):
    """Return text capped at ``maximum`` UTF-8 bytes without splitting a codepoint."""

    text = text or ""
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum:
        return text
    return encoded[:maximum].decode("utf-8", errors="ignore").rstrip()


def record_source_received_at(record):
    """Return a comparable, timezone-aware source timestamp for a mail record.

    Gmail records carry ``internal_date_ms``. Apple Mail records normally carry
    a parsed ``received_date``. The helper keeps freshness resolution
    independent from either transport while avoiding the sender-controlled
    RFC ``Date`` header when Gmail metadata is available.
    """

    value = record.get("source_received_at")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(LOCAL_TIMEZONE_NAME))
        return value
    if isinstance(value, date):
        return datetime.combine(
            value,
            datetime_time.min,
            tzinfo=ZoneInfo(LOCAL_TIMEZONE_NAME),
        )

    for key in ("internal_date_ms", "source_received_at_ms"):
        raw_value = record.get(key)
        if raw_value is None:
            continue
        try:
            return datetime.fromtimestamp(
                int(raw_value) / 1000,
                ZoneInfo(LOCAL_TIMEZONE_NAME),
            )
        except (TypeError, ValueError, OverflowError, OSError):
            continue

    received_date = record.get("received_date")
    if isinstance(received_date, datetime):
        if received_date.tzinfo is None:
            return received_date.replace(tzinfo=ZoneInfo(LOCAL_TIMEZONE_NAME))
        return received_date
    if isinstance(received_date, date):
        return datetime.combine(
            received_date,
            datetime_time.min,
            tzinfo=ZoneInfo(LOCAL_TIMEZONE_NAME),
        )
    if isinstance(received_date, str):
        try:
            parsed = datetime.fromisoformat(received_date)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(LOCAL_TIMEZONE_NAME))
            return parsed
    return None


def strip_quoted_reply(text):
    """Remove common quoted-reply lines before semantic date extraction."""
    lines = (text or "").splitlines()

    def starts_thread_header_block(index):
        first = THREAD_HEADER_RE.match(lines[index].strip())
        if first is None or first.group("name").casefold() not in THREAD_START_HEADERS:
            return False
        header_names = set()
        for candidate in lines[index:index + 7]:
            match = THREAD_HEADER_RE.match(candidate.strip())
            if match is not None:
                header_names.add(match.group("name").casefold())
        return len(header_names) >= 2

    kept_lines = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if kept_lines and QUOTED_REPLY_HEADER_RE.match(stripped):
            break
        if kept_lines and starts_thread_header_block(index):
            break
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()
