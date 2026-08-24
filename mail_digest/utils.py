"""Shared transport and text-cleaning helpers."""

import re

from .config import TRANSPORT_NEWLINE_TOKEN

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
