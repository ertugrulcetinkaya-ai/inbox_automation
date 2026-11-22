"""Telegram delivery adapter."""

import random
import time

import requests

from ..config import load_env, log


TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
TELEGRAM_MAX_ATTEMPTS = 3
TELEGRAM_MAX_RETRY_DELAY_SECONDS = 30.0
TELEGRAM_MESSAGE_MAX_CHARS = 3500


def _split_message(message):
    """Split on line breaks where possible and hard-cut oversized lines."""

    chunks = []
    start = 0
    while start < len(message):
        end = min(start + TELEGRAM_MESSAGE_MAX_CHARS, len(message))
        if end < len(message):
            newline = message.rfind("\n", start, end)
            if newline >= start:
                end = newline + 1
        chunks.append(message[start:end])
        start = end
    return chunks


def _retry_delay_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    if delay < 0:
        return None
    return min(delay, TELEGRAM_MAX_RETRY_DELAY_SECONDS)


def _retry_after(response):
    payload = None
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            payload = json_method()
        except (AttributeError, TypeError, ValueError):
            payload = None
    if isinstance(payload, dict):
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            delay = _retry_delay_value(parameters.get("retry_after"))
            if delay is not None:
                return delay

    headers = getattr(response, "headers", {}) or {}
    value = None
    if hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
    return _retry_delay_value(value)


def _retry_delay(response, attempt):
    if getattr(response, "status_code", None) == 429:
        retry_after = _retry_after(response)
        if retry_after is not None:
            return retry_after
    return min(
        TELEGRAM_MAX_RETRY_DELAY_SECONDS,
        0.5 * (2 ** (attempt - 1)) + random.uniform(0, 0.1),
    )


def _send_chunk(token, chat_id, chunk, chunk_number, chunk_count, sleep=time.sleep):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": chunk},
                timeout=15,
            )
        except Exception as exc:
            retryable = isinstance(exc, (requests.RequestException, OSError, TimeoutError))
            if retryable and attempt < TELEGRAM_MAX_ATTEMPTS:
                sleep(_retry_delay(None, attempt))
                continue
            log(
                f"Network error sending chunk {chunk_number}/{chunk_count} "
                f"({exc.__class__.__name__})"
            )
            return False

        status = getattr(response, "status_code", None)
        if status == 200:
            try:
                response_payload = response.json()
            except (AttributeError, ValueError):
                response_payload = None
            if isinstance(response_payload, dict) and response_payload.get("ok") is True:
                return True
            log(f"Telegram Error (Chunk {chunk_number}/{chunk_count}): invalid response")
            return False

        if status in TRANSIENT_HTTP_STATUSES and attempt < TELEGRAM_MAX_ATTEMPTS:
            sleep(_retry_delay(response, attempt))
            continue
        log(f"Telegram Error (Chunk {chunk_number}/{chunk_count}): {status or 'unknown'}")
        return False
    return False


def send_text(token, chat_id, text, sleep=None) -> bool:
    """Send text through Telegram, preserving chunking and bounded retries.

    Telegram has no transaction spanning multiple messages. If a later chunk
    fails, already delivered chunks may be repeated on the next run; the CLI
    deliberately does not advance reminder state for that partial delivery.
    """

    sleep = sleep or time.sleep
    if not token or not chat_id:
        log("Missing Telegram credentials in env file.")
        return False

    chunks = _split_message(text)
    for index, chunk in enumerate(chunks):
        if not _send_chunk(
            token,
            chat_id,
            chunk,
            index + 1,
            len(chunks),
            sleep=sleep,
        ):
            return False
    return True


def send_telegram(message, sleep=None):
    """Load configured Telegram credentials and send a digest message."""

    env = load_env()
    return send_text(
        env.get("TELEGRAM_BOT_TOKEN"),
        env.get("TELEGRAM_CHAT_ID"),
        message,
        sleep=sleep,
    )
