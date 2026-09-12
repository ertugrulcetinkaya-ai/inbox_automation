"""Persistent deduplication for successfully delivered meeting reminders."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import LOCAL_TIMEZONE_NAME, REMINDER_STATE_FILE, local_now


SENT_REMINDERS_KEY = "sent"
LAST_SUCCESSFUL_SCAN_KEY = "last_successful_reminder_scan"


def reminder_key(meeting):
    """Return a privacy-preserving stable key for one meeting occurrence."""

    identity = "|".join(
        (
            meeting.get("uid") or meeting.get("source_message_id") or "",
            meeting["date"].isoformat(),
            str(meeting["sort_minutes"]),
            meeting.get("subject", ""),
            meeting.get("sender", ""),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME)).replace(tzinfo=None)
    return parsed


def load_reminder_state(state_file=None, now=None):
    """Load sent hashes and the last completed reminder scan.

    Older installations stored the sent hashes as a flat JSON object. That
    format remains readable while new writes use an envelope carrying the
    catch-up cursor.
    """

    path = Path(state_file or REMINDER_STATE_FILE).expanduser()
    now = now or local_now()
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo(LOCAL_TIMEZONE_NAME)).replace(tzinfo=None)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {SENT_REMINDERS_KEY: {}, LAST_SUCCESSFUL_SCAN_KEY: None}
    if not isinstance(payload, dict):
        return {SENT_REMINDERS_KEY: {}, LAST_SUCCESSFUL_SCAN_KEY: None}

    if SENT_REMINDERS_KEY in payload:
        raw_sent = payload.get(SENT_REMINDERS_KEY)
        last_successful_scan = _parse_timestamp(payload.get(LAST_SUCCESSFUL_SCAN_KEY))
    else:
        raw_sent = payload
        last_successful_scan = None
    if not isinstance(raw_sent, dict):
        raw_sent = {}

    cutoff = now - timedelta(days=40)
    retained = {}
    for key, timestamp in raw_sent.items():
        if not isinstance(key, str) or not isinstance(timestamp, str):
            continue
        sent_at = _parse_timestamp(timestamp)
        if sent_at is None:
            continue
        if sent_at >= cutoff:
            retained[key] = timestamp
    return {
        SENT_REMINDERS_KEY: retained,
        LAST_SUCCESSFUL_SCAN_KEY: last_successful_scan,
    }


def load_sent_reminders(state_file=None, now=None):
    return load_reminder_state(state_file=state_file, now=now)[SENT_REMINDERS_KEY]


def load_last_successful_reminder_scan(state_file=None, now=None):
    return load_reminder_state(
        state_file=state_file,
        now=now,
    )[LAST_SUCCESSFUL_SCAN_KEY]


def save_reminder_state(sent, last_successful_scan=None, state_file=None):
    path = Path(state_file or REMINDER_STATE_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    payload = json.dumps(
        {
            SENT_REMINDERS_KEY: sent,
            LAST_SUCCESSFUL_SCAN_KEY: (
                last_successful_scan.isoformat(timespec="seconds")
                if isinstance(last_successful_scan, datetime)
                else None
            ),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        # mkstemp creates the file as 0600; keep the explicit mode assertion in
        # case the platform's default ever changes.
        temporary.chmod(0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def save_sent_reminders(sent, state_file=None):
    """Compatibility wrapper for callers that only persist sent hashes."""

    save_reminder_state(
        sent,
        last_successful_scan=load_last_successful_reminder_scan(state_file=state_file),
        state_file=state_file,
    )


def mark_reminders_sent(
    sent,
    meetings,
    now=None,
    state_file=None,
    last_successful_scan=None,
):
    now = now or local_now()
    updated = dict(sent)
    timestamp = now.isoformat(timespec="seconds")
    for meeting in meetings:
        updated[reminder_key(meeting)] = timestamp
    if last_successful_scan is None:
        last_successful_scan = load_last_successful_reminder_scan(state_file=state_file)
    save_reminder_state(
        updated,
        last_successful_scan=last_successful_scan,
        state_file=state_file,
    )
    return updated


def mark_reminder_scan_succeeded(scan_at=None, state_file=None):
    """Advance the catch-up cursor after a completed, non-dry-run scan."""

    scan_at = scan_at or local_now()
    sent = load_sent_reminders(state_file=state_file, now=scan_at)
    save_reminder_state(
        sent,
        last_successful_scan=scan_at,
        state_file=state_file,
    )
    return scan_at
