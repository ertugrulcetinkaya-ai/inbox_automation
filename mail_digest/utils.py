"""Shared transport and text-cleaning helpers."""

import re

from .config import TRANSPORT_NEWLINE_TOKEN


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
