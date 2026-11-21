"""Local-only health report for the mail digest runtime."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..config import (
    LOCAL_TIMEZONE_NAME,
    REMINDER_STATE_FILE,
    gmail_cache_file,
    gmail_token_file,
    local_now,
)
from ..sources import selected_source_name
from .reminder_state import load_last_successful_reminder_scan
from .run_status import status_file_path


STATUS_MODES = (
    "daily",
    "daily_scheduled",
    "upcoming",
    "weekly",
    "attention",
    "reminder",
)


def _local_naive(value):
    if value.tzinfo is not None:
        return value.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME)).replace(tzinfo=None)
    return value


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return _local_naive(datetime.fromisoformat(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _read_status():
    path = status_file_path()
    if not path.is_file():
        return {}, "yok"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, UnicodeError):
        return {}, "bozuk veya okunamıyor"
    if not isinstance(payload, dict) or not isinstance(payload.get("modes"), dict):
        return {}, "bozuk veya okunamıyor"
    return payload["modes"], "var"


def _token_status(now):
    path = gmail_token_file().expanduser()
    problems = []
    if not path.is_file():
        return "dosya yok", ["token dosyası yok"]

    try:
        permissions = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return "dosya okunamıyor", ["token dosyasının izinleri okunamıyor"]
    private = permissions == 0o600
    if not private:
        problems.append("token dosyasının izni 0600 değil")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, UnicodeError):
        return "JSON bozuk", problems + ["token JSON okunamıyor"]
    if not isinstance(payload, dict):
        return "JSON bozuk", problems + ["token JSON okunamıyor"]

    has_refresh_token = isinstance(payload.get("refresh_token"), str) and bool(
        payload["refresh_token"].strip()
    )
    if not has_refresh_token:
        problems.append("refresh token yok")

    raw_expiry = payload.get("expiry")
    expiry = _parse_timestamp(raw_expiry)
    if expiry is None:
        expiry_text = "bilinmiyor"
        problems.append("token süresi okunamıyor")
    else:
        expiry_text = expiry.strftime("%Y-%m-%d %H:%M")
        if expiry <= now:
            problems.append("token süresi dolmuş")

    state = "sorun var" if problems else "uygun"
    return (
        f"{state}; dosya: var; 0600: {'evet' if private else 'hayır'}; "
        f"refresh token: {'var' if has_refresh_token else 'yok'}; süre: {expiry_text}",
        problems,
    )


def _cache_status(now):
    path = gmail_cache_file().expanduser()
    if not path.is_file():
        return None, None, "yok"

    encoded_path = quote(str(path.absolute()), safe="/:\\")
    connection = None
    try:
        connection = sqlite3.connect(f"file:{encoded_path}?mode=ro", uri=True)
        updated_row = connection.execute(
            "SELECT value FROM metadata WHERE key='updated_at_ms'"
        ).fetchone()
        count_row = connection.execute("SELECT COUNT(*) FROM messages").fetchone()
        updated_at = None
        if updated_row:
            updated_ms = int(updated_row[0])
            updated_at = datetime.fromtimestamp(
                updated_ms / 1000,
                ZoneInfo(LOCAL_TIMEZONE_NAME),
            ).replace(tzinfo=None)
        message_count = int(count_row[0]) if count_row else 0
        age = now - updated_at if updated_at is not None else None
        return updated_at, message_count, age
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
        return None, None, "bozuk veya okunamıyor"
    finally:
        if connection is not None:
            connection.close()


def _format_time(value):
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "yok"


def _scheduled_daily_status(modes, now):
    if now.time() < datetime_time(8, 0):
        return "henüz zamanı gelmedi", False

    entry = modes.get("daily_scheduled")
    if not isinstance(entry, dict):
        entry = {}
    success = _parse_timestamp(entry.get("last_success_at"))
    failure = _parse_timestamp(entry.get("last_failure_at"))
    skipped = _parse_timestamp(entry.get("last_skipped_lock_at"))
    success_today = success if success is not None and success.date() == now.date() else None
    failure_today = failure if failure is not None and failure.date() == now.date() else None
    skipped_today = skipped if skipped is not None and skipped.date() == now.date() else None

    if success_today is not None and success_today >= max(
        (value for value in (failure_today, skipped_today) if value is not None),
        default=success_today,
    ):
        return "gönderildi", True
    if skipped_today is not None and skipped_today >= max(
        (value for value in (success_today, failure_today) if value is not None),
        default=skipped_today,
    ):
        return "atlandı (kilit dolu)", False
    if failure_today is not None:
        return "başarısız", False
    return "henüz gönderilmedi", False


def build_status_report(now=None):
    """Build a Turkish runtime report without network access or token refresh."""

    now = _local_naive(now or local_now())
    modes, status_file_state = _read_status()
    lines = [
        f"Durum raporu — {now:%Y-%m-%d %H:%M}",
        f"Mail kaynağı: {selected_source_name()}",
        f"Çalışma kaydı: {status_file_state}",
        "Son çalışmalar:",
    ]
    for mode in STATUS_MODES:
        entry = modes.get(mode)
        if not isinstance(entry, dict):
            entry = {}
        success = _parse_timestamp(entry.get("last_success_at"))
        failure = _parse_timestamp(entry.get("last_failure_at"))
        detail = (
            f"• {mode}: son başarı {_format_time(success)}; "
            f"son hata {_format_time(failure)}"
        )
        error_type = entry.get("last_error_type")
        if isinstance(error_type, str) and error_type.isidentifier():
            detail += f" ({error_type})"
        skipped = _parse_timestamp(entry.get("last_skipped_lock_at"))
        if skipped is not None:
            detail += f"; skipped_lock {_format_time(skipped)}"
        lines.append(detail)

    scheduled_status, scheduled_succeeded = _scheduled_daily_status(modes, now)
    lines.append(f"Planlı günlük özet (08:00): {scheduled_status}")

    token_text, token_problems = _token_status(now)
    lines.append(f"Gmail token: {token_text}")

    cache_updated_at, cache_message_count, cache_age = _cache_status(now)
    if isinstance(cache_age, timedelta):
        lines.append(
            f"Gmail önbelleği: son güncelleme {_format_time(cache_updated_at)}; "
            f"mesaj sayısı {cache_message_count}"
        )
    elif cache_age == "bozuk veya okunamıyor":
        lines.append("Gmail önbelleği: bozuk veya okunamıyor")
    elif cache_message_count is not None:
        lines.append(
            f"Gmail önbelleği: son güncelleme yok; mesaj sayısı {cache_message_count}"
        )
    else:
        lines.append("Gmail önbelleği: yok")

    reminder_path = Path(
        os.environ.get("MEETING_REMINDER_STATE_FILE", str(REMINDER_STATE_FILE))
    ).expanduser()
    reminder_scan = load_last_successful_reminder_scan(
        state_file=reminder_path,
        now=now,
    )
    lines.append(f"Son hatırlatma taraması: {_format_time(reminder_scan)}")

    warnings = []
    if now.time() >= datetime_time(8, 0) and not scheduled_succeeded:
        warnings.append(
            f"⚠️ bugünkü planlı özet gönderilmedi: {scheduled_status}"
        )
    if not isinstance(cache_age, timedelta) or cache_age > timedelta(minutes=30):
        warnings.append("⚠️ Gmail önbelleği 30 dakikadan eski veya yok")
    if reminder_scan is None or now - reminder_scan > timedelta(minutes=20):
        warnings.append("⚠️ hatırlatma taraması 20 dakikadan eski veya hiç yok")
    if token_problems:
        warnings.append("⚠️ Gmail token sorunlu: " + ", ".join(token_problems))
    lines.append("Uyarılar:")
    lines.extend(warnings or ["Yok"])
    return "\n".join(lines)
