"""Telegram delivery adapter."""

import random
import time

import requests

from ..config import load_env, log


TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
TELEGRAM_MAX_ATTEMPTS = 3
TELEGRAM_MAX_RETRY_DELAY_SECONDS = 30.0


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
            value = parameters.get("retry_after")
            try:
                delay = float(value)
            except (TypeError, ValueError):
                delay = None
            if delay is not None and delay >= 0:
                return min(delay, TELEGRAM_MAX_RETRY_DELAY_SECONDS)

    headers = getattr(response, "headers", {}) or {}
    value = None
    if hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if delay < 0:
        return None
    return min(delay, TELEGRAM_MAX_RETRY_DELAY_SECONDS)


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
            safe_error = str(exc).replace(token, "[REDACTED]")
            log(f"Network error sending chunk {chunk_number}/{chunk_count}: {safe_error}")
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


def send_telegram(message, sleep=None):
    """Send every chunk or fail closed without recording delivery success.

    Telegram has no transaction spanning multiple messages. If a later chunk
    fails, already delivered chunks may be repeated on the next run; the CLI
    deliberately does not advance reminder state for that partial delivery.
    """

    sleep = sleep or time.sleep
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log("Missing Telegram credentials in env file.")
        return False

    chunks = [message[i:i + 3500] for i in range(0, len(message), 3500)]
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
