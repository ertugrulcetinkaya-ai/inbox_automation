"""Persistent deduplication for successfully delivered meeting reminders."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from ..config import REMINDER_STATE_FILE, local_now


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


def load_sent_reminders(state_file=None, now=None):
    path = Path(state_file or REMINDER_STATE_FILE).expanduser()
    now = now or local_now()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    cutoff = now - timedelta(days=40)
    retained = {}
    for key, timestamp in payload.items():
        if not isinstance(key, str) or not isinstance(timestamp, str):
            continue
        try:
            sent_at = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        if sent_at >= cutoff:
            retained[key] = timestamp
    return retained


def save_sent_reminders(sent, state_file=None):
    path = Path(state_file or REMINDER_STATE_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(sent, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def mark_reminders_sent(sent, meetings, now=None, state_file=None):
    now = now or local_now()
    updated = dict(sent)
    timestamp = now.isoformat(timespec="seconds")
    for meeting in meetings:
        updated[reminder_key(meeting)] = timestamp
    save_sent_reminders(updated, state_file=state_file)
    return updated
