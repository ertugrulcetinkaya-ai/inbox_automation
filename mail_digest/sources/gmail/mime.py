"""Normalize Gmail FULL payloads into the existing parser record contract."""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime
from email import policy
from email.header import decode_header
from email.parser import Parser

from ...config import (
    LOCAL_TIMEZONE_NAME,
    MAX_BODY_BYTES,
    MAX_ICS_BYTES,
    MAX_RAW_MIME_BYTES,
    TARGET_EMAIL,
)
from ...utils import limit_utf8_bytes, sanitize_content, sanitize_transport_field
from .api import GmailApiError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


class MessageStructureError(ValueError):
    """A critical Gmail field is absent or unusable."""


class MessageSizeError(MessageStructureError):
    """A decoded Gmail body exceeds the local parsing/storage policy."""


ICS_TEXT_MARKERS = ("begin:vcalendar", "begin:vevent", "method:request", "method:publish", "method:cancel")


def decode_base64url(data, max_bytes=None):
    if not isinstance(data, str):
        raise ValueError("body data is not text")
    if max_bytes is not None:
        # Reject oversized payloads before base64 decoding allocates the full
        # byte string. The post-decode check below handles unpadded input and
        # any rounding edge cases.
        encoded_limit = ((max_bytes + 2) * 4) // 3 + 4
        if len(data) > encoded_limit:
            raise MessageSizeError("Gmail payload exceeds the configured size limit")
    padded = data + "=" * (-len(data) % 4)
    decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    if max_bytes is not None and len(decoded) > max_bytes:
        raise MessageSizeError("Gmail payload exceeds the configured size limit")
    return decoded


def _decode_header(value):
    chunks = []
    for chunk, charset in decode_header(value or ""):
        if isinstance(chunk, bytes):
            for encoding in (charset, "utf-8", "latin-1"):
                if not encoding:
                    continue
                try:
                    chunk = chunk.decode(encoding)
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
        chunks.append(str(chunk))
    return sanitize_transport_field("".join(chunks))


def _headers(payload):
    result = {}
    items = payload.get("headers", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).casefold()
        if name and name not in result:
            try:
                result[name] = _decode_header(item.get("value", ""))
            except (LookupError, TypeError, UnicodeError, ValueError):
                result[name] = sanitize_transport_field(str(item.get("value", "")))
    return result


def _walk_parts(part):
    if not isinstance(part, dict):
        return
    yield part
    for child in part.get("parts", []) or []:
        yield from _walk_parts(child)


def _is_attachment(part):
    filename = str(part.get("filename") or "")
    headers = _headers(part)
    disposition = headers.get("content-disposition", "").casefold()
    return bool(filename) or disposition.startswith("attachment")


def _charset(part):
    headers = _headers(part)
    content_type = headers.get("content-type", "")
    match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _decode_text_part(part, message_id, attachment_loader, max_bytes=MAX_BODY_BYTES):
    body = part.get("body") or {}
    data = body.get("data")
    if not data and body.get("attachmentId"):
        attachment = attachment_loader(message_id, body["attachmentId"])
        data = (attachment or {}).get("data")
    if not data:
        return ""
    raw = decode_base64url(data, max_bytes=max_bytes)
    charset = _charset(part)
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


class _HTMLTextParser:
    """Tiny line-preserving HTML converter without an extra dependency."""

    BLOCK_RE = re.compile(
        r"(?is)<\s*(?:br\s*/?|/\s*(?:p|div|li|tr|h[1-6])|(?:p|div|li|tr|h[1-6])\b[^>]*)>"
    )

    @classmethod
    def convert(cls, value):
        value = re.sub(r"(?is)<(?:script|style)\b.*?</(?:script|style)\s*>", "", value or "")
        value = cls.BLOCK_RE.sub("\n", value)
        value = re.sub(r"(?s)<[^>]*>", "", value)
        value = html.unescape(value)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)


def _calendar_metadata_signal(parts):
    for part in parts:
        mime_type = str(part.get("mimeType") or "").casefold()
        filename = str(part.get("filename") or "").casefold()
        if mime_type == "text/calendar" or filename.endswith(".ics"):
            return True
    return False


def _calendar_payloads_from_raw_source(raw_text):
    """Extract only calendar parts from a bounded raw MIME message."""

    payloads = []
    try:
        message = Parser(policy=policy.default).parsestr(raw_text)
    except (TypeError, ValueError):
        message = None
    if message is not None:
        for part in message.walk():
            content_type = (part.get_content_type() or "").casefold()
            filename = (part.get_filename() or "").casefold()
            if content_type != "text/calendar" and not filename.endswith(".ics"):
                continue
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                if len(payload) > MAX_ICS_BYTES:
                    continue
                charset = part.get_content_charset() or "utf-8"
                payload = payload.decode(charset, errors="replace")
            elif not isinstance(payload, str):
                continue
            payload = limit_utf8_bytes(payload, MAX_ICS_BYTES)
            if payload:
                payloads.append(payload)
    if payloads:
        return payloads

    # Some providers return an ICS document without a MIME Content-Type. Keep
    # the line structure but discard surrounding plaintext/headers.
    return [
        limit_utf8_bytes(match.group(0), MAX_ICS_BYTES)
        for match in re.finditer(
            r"(?is)BEGIN:VCALENDAR.*?END:VCALENDAR", raw_text or ""
        )
        if match.group(0)
    ]


def _received_date_text(headers, internal_date_ms):
    timestamp = internal_date_ms / 1000
    value = datetime.fromtimestamp(timestamp, ZoneInfo(LOCAL_TIMEZONE_NAME) if ZoneInfo else None)
    return value.strftime("%a, %d %b %Y %H:%M:%S %z")


def normalize_message(message, attachment_loader, raw_loader):
    message_id = message.get("id") if isinstance(message, dict) else None
    if not isinstance(message_id, str) or not message_id:
        raise MessageStructureError("missing Gmail message id")
    try:
        internal_date_ms = int(message["internalDate"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MessageStructureError("invalid internalDate") from exc
    labels = message.get("labelIds")
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise MessageStructureError("invalid labelIds")

    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = _headers(payload)
    parts = list(_walk_parts(payload))
    plain_parts = [
        part for part in parts
        if str(part.get("mimeType") or "").casefold() == "text/plain" and not _is_attachment(part)
    ]
    html_parts = [
        part for part in parts
        if str(part.get("mimeType") or "").casefold() == "text/html" and not _is_attachment(part)
    ]

    content = ""
    for candidates, is_html in ((plain_parts, False), (html_parts, True)):
        if content:
            break
        for part in candidates:
            try:
                candidate = _decode_text_part(
                    part,
                    message_id,
                    attachment_loader,
                    max_bytes=MAX_BODY_BYTES,
                )
            except GmailApiError:
                raise
            except (KeyError, LookupError, MessageStructureError, TypeError, UnicodeError, ValueError):
                continue
            if candidate:
                content = _HTMLTextParser.convert(candidate) if is_html else candidate
                break
    content = limit_utf8_bytes(sanitize_content(content), MAX_BODY_BYTES)

    calendar_signal = _calendar_metadata_signal(parts) or any(
        marker in content.casefold() for marker in ICS_TEXT_MARKERS
    )
    raw_source = ""
    if calendar_signal:
        try:
            raw_response = raw_loader(message_id)
            raw_bytes = decode_base64url(
                (raw_response or {}).get("raw", ""),
                max_bytes=MAX_RAW_MIME_BYTES,
            )
            raw_text = raw_bytes.decode(
                "utf-8", errors="replace"
            )
            raw_source = "\n".join(_calendar_payloads_from_raw_source(raw_text))
            raw_source = limit_utf8_bytes(sanitize_content(raw_source), MAX_ICS_BYTES)
        except GmailApiError:
            raise
        except (KeyError, LookupError, MessageStructureError, TypeError, UnicodeError, ValueError):
            raw_source = ""

    received_text = _received_date_text(headers, internal_date_ms)
    received_at = datetime.fromtimestamp(
        internal_date_ms / 1000,
        ZoneInfo(LOCAL_TIMEZONE_NAME) if ZoneInfo else None,
    )
    received_date = received_at.date()
    rfc_message_id = headers.get("message-id", "").strip()
    source_message_id = (
        rfc_message_id
        if re.fullmatch(r"<[^<>\s]+>", rfc_message_id)
        else f"gmail:{message_id}"
    )
    return {
        "gmail_message_id": message_id,
        "thread_id": str(message.get("threadId") or ""),
        "internal_date_ms": internal_date_ms,
        "account": TARGET_EMAIL,
        "mailbox": "INBOX",
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": received_text,
        "received_date": received_date,
        "received_local_date": received_date.isoformat(),
        "source_received_at": received_at,
        "content": content,
        "source_message_id": source_message_id,
        "raw_source": raw_source,
    }
