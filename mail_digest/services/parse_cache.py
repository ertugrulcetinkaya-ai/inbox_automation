"""Persistent, private cache for deterministic meeting parse results."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import local_now
from ..parsing import PARSER_VERSION


DEFAULT_PARSE_CACHE_FILE = (
    Path.home() / ".hermes_local_automation" / "mail_digest" / "parse_cache.sqlite3"
)
_RECORD_KEY_FIELDS = (
    "subject",
    "content",
    "raw_source",
    "date",
    "received_date",
    "sender",
    "thread_id",
    "source_message_id",
    "source_received_at",
    "internal_date_ms",
)


class ParseCacheUnavailable(RuntimeError):
    """Raised when the optional parse cache cannot be safely used."""


def _encode(value):
    if isinstance(value, datetime):
        return {
            "__type__": "datetime",
            "value": value.isoformat(),
            "zone": getattr(value.tzinfo, "key", None),
            "fold": value.fold,
        }
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, timedelta):
        return {
            "__type__": "timedelta",
            "days": value.days,
            "seconds": value.seconds,
            "microseconds": value.microseconds,
        }
    if isinstance(value, dict):
        return {
            "__type__": "dict",
            "items": {str(key): _encode(item) for key, item in value.items()},
        }
    if isinstance(value, list):
        return {"__type__": "list", "items": [_encode(item) for item in value]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported cached value type: {type(value).__name__}")


def _decode(value):
    if not isinstance(value, dict) or "__type__" not in value:
        return value
    value_type = value["__type__"]
    if value_type == "datetime":
        result = datetime.fromisoformat(value["value"])
        fold = int(value.get("fold", 0))
        zone = value.get("zone")
        if zone:
            return result.replace(tzinfo=ZoneInfo(zone), fold=fold)
        return result.replace(fold=fold)
    if value_type == "date":
        return date.fromisoformat(value["value"])
    if value_type == "timedelta":
        return timedelta(
            days=int(value["days"]),
            seconds=int(value["seconds"]),
            microseconds=int(value["microseconds"]),
        )
    if value_type == "dict":
        return {key: _decode(item) for key, item in value["items"].items()}
    if value_type == "list":
        return [_decode(item) for item in value["items"]]
    raise ValueError("Unknown parse cache value type")


def _json_value(value):
    return json.dumps(_encode(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_key(
    record,
    start_date,
    end_date,
    include_cancelled,
    include_lifecycle_outside_range,
):
    key_data = {
        "parser_version": PARSER_VERSION,
        "record": {field: record.get(field) for field in _RECORD_KEY_FIELDS},
        "start_date": start_date,
        "end_date": end_date,
        "include_cancelled": include_cancelled,
        "include_lifecycle_outside_range": include_lifecycle_outside_range,
    }
    try:
        encoded = json.dumps(
            _encode(key_data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ParseCacheUnavailable from exc


class ParseCache:
    """One SQLite connection for a collection pass and one write transaction."""

    def __init__(self, path=None):
        configured_path = path or os.environ.get(
            "MAIL_DIGEST_PARSE_CACHE_FILE", str(DEFAULT_PARSE_CACHE_FILE)
        )
        self.path = Path(configured_path).expanduser()
        self.connection: sqlite3.Connection | None = None

    def __enter__(self):
        try:
            parent_existed = self.path.parent.exists()
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not parent_existed or self.path == DEFAULT_PARSE_CACHE_FILE:
                self.path.parent.chmod(0o700)
            elif stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700:
                raise ParseCacheUnavailable("Parse cache directory must have 0700 permissions")
            connection = sqlite3.connect(str(self.path), timeout=2.0)
            self.connection = connection
            self.path.chmod(0o600)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS parse_results ("
                "cache_key TEXT PRIMARY KEY, start_date TEXT NOT NULL, result_json TEXT NOT NULL)"
            )
            return self
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._close()
            raise ParseCacheUnavailable from exc

    def __exit__(self, exc_type, exc_value, traceback):
        self._close()

    def _close(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _require_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise ParseCacheUnavailable("Parse cache is not open")
        return self.connection

    def get(self, key):
        try:
            row = self._require_connection().execute(
                "SELECT result_json FROM parse_results WHERE cache_key=?", (key,)
            ).fetchone()
            return None if row is None else _decode(json.loads(row[0]))
        except (sqlite3.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ParseCacheUnavailable from exc

    def store(self, start_date, entries):
        """Prune stale range entries and persist this pass's misses atomically."""

        cutoff = (local_now().date() - timedelta(days=2)).isoformat()
        try:
            rows = [
                (key, start_date.isoformat(), _json_value(result))
                for key, result in entries.items()
            ]
            connection = self._require_connection()
            with connection:
                connection.execute(
                    "DELETE FROM parse_results WHERE start_date < ?", (cutoff,)
                )
                connection.executemany(
                    "INSERT OR REPLACE INTO parse_results(cache_key, start_date, result_json) "
                    "VALUES (?, ?, ?)",
                    rows,
                )
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            raise ParseCacheUnavailable from exc
