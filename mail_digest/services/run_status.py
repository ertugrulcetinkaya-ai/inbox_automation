"""Privacy-safe, atomic status records for digest runs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import log, local_now
from ..fileio import atomic_write_private


DEFAULT_STATUS_FILE = (
    Path.home() / ".hermes_local_automation" / "mail_digest" / "status.json"
)


def status_file_path():
    return Path(
        os.environ.get("MAIL_DIGEST_STATUS_FILE", str(DEFAULT_STATUS_FILE))
    ).expanduser()


def _read_status(path):
    if not path.exists():
        return {"modes": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, UnicodeError) as exc:
        log(f"Run status could not be read ({exc.__class__.__name__})")
        return {"modes": {}}
    if not isinstance(payload, dict):
        return {"modes": {}}
    modes = payload.get("modes")
    if not isinstance(modes, dict):
        payload["modes"] = {}
    return payload


def _write_status(path, payload):
    atomic_write_private(
        path,
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n",
    )


def _record(mode, update):
    path = status_file_path()
    try:
        payload = _read_status(path)
        modes = payload.setdefault("modes", {})
        if not isinstance(modes, dict):
            modes = {}
            payload["modes"] = modes
        entry = modes.get(mode)
        if not isinstance(entry, dict):
            entry = {
                "last_started_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error_type": None,
                "last_skipped_lock_at": None,
            }
            modes[mode] = entry
        entry.update(update)
        _write_status(path, payload)
    except Exception as exc:
        log(f"Run status could not be saved ({exc.__class__.__name__})")


def _timestamp(value=None):
    value = value or local_now()
    return value.isoformat(timespec="seconds")


def record_run_started(mode, started_at=None):
    _record(mode, {"last_started_at": _timestamp(started_at)})


def record_run_finished(mode, succeeded, finished_at=None, error_type=None):
    timestamp = _timestamp(finished_at)
    if succeeded:
        _record(mode, {"last_success_at": timestamp})
        return
    error_name = error_type if isinstance(error_type, str) else getattr(
        error_type, "__name__", "DigestFailed"
    )
    if not error_name.isidentifier():
        error_name = "DigestFailed"
    _record(
        mode,
        {
            "last_failure_at": timestamp,
            "last_error_type": error_name,
        },
    )


def record_run_skipped_lock(mode, skipped_at=None):
    timestamp = _timestamp(skipped_at)
    _record(
        mode,
        {
            "last_failure_at": timestamp,
            "last_error_type": "skipped_lock",
            "last_skipped_lock_at": timestamp,
        },
    )
