"""Persistent parse-cache behavior and safety checks."""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from mail_digest.config import local_now
from mail_digest.parsing.meeting_parser import extract_meetings
from mail_digest.services import meeting_service, parse_cache
from mail_digest.services.meeting_service import build_digest_result, due_reminder_meetings


def event(
    uid,
    start,
    *,
    sequence=0,
    recurrence_id=None,
    recurrence=None,
    status=None,
    title="Synthetic cache meeting",
):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SEQUENCE:{sequence}", start]
    if recurrence_id:
        lines.append(recurrence_id)
    if recurrence:
        lines.append(recurrence)
    if status:
        lines.append(f"STATUS:{status}")
    lines.extend((f"SUMMARY:{title}", "END:VEVENT"))
    return lines


def ics_record(*events, subject="Synthetic calendar meeting", source_id="calendar@example.test"):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST"]
    for value in events:
        lines.extend(value)
    lines.append("END:VCALENDAR")
    return {
        "subject": subject,
        "sender": "Synthetic Organizer <organizer@example.test>",
        "received_date": "2026-11-21T09:00:00",
        "source_message_id": source_id,
        "content": "\n".join(lines),
    }


def cache_records():
    master = ics_record(
        event(
            "daily-series",
            "DTSTART;TZID=Europe/Istanbul:20261123T100000",
            recurrence="RRULE:FREQ=DAILY;COUNT=5",
        ),
        source_id="master@example.test",
    )
    changed = ics_record(
        event(
            "daily-series",
            "DTSTART;TZID=Europe/Istanbul:20261125T120000",
            sequence=2,
            recurrence_id="RECURRENCE-ID;TZID=Europe/Istanbul:20261125T100000",
            title="Synthetic changed occurrence",
        ),
        source_id="update@example.test",
    )
    cancelled = ics_record(
        event(
            "daily-series",
            "DTSTART;TZID=Europe/Istanbul:20261124T100000",
            sequence=2,
            recurrence_id="RECURRENCE-ID;TZID=Europe/Istanbul:20261124T100000",
            status="CANCELLED",
        ),
        source_id="cancel@example.test",
    )
    semantic = {
        "subject": "Synthetic meeting review",
        "sender": "Synthetic Organizer <organizer@example.test>",
        "received_date": "2026-11-21",
        "source_message_id": "semantic@example.test",
        "content": "Meeting review on November 23, 2026 at 13:00.",
    }
    return [master, changed, cancelled, semantic]


class ParseCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp.name) / "parse-cache.sqlite3"
        self.env = patch.dict(
            os.environ,
            {"MAIL_DIGEST_PARSE_CACHE_FILE": str(self.cache_path)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def test_cached_and_uncached_results_match_across_digest_modes(self):
        records = cache_records()
        target = date(2026, 11, 23)

        for mode in ("daily", "weekly", "upcoming"):
            with self.subTest(mode=mode):
                cached = build_digest_result(records, mode, target_date=target)
                with patch.object(meeting_service, "ParseCache", side_effect=parse_cache.ParseCacheUnavailable):
                    uncached = build_digest_result(records, mode, target_date=target)
                self.assertEqual(cached, uncached)
                self.assertNotIn(
                    date(2026, 11, 24), [meeting["date"] for meeting in cached.meetings]
                )
                if mode == "weekly":
                    self.assertIn(
                        "Synthetic changed occurrence", str(cached.meetings)
                    )

        reminder_now = datetime(2026, 11, 23, 9, 50)
        cached_reminders = due_reminder_meetings(records, now=reminder_now)
        with patch.object(meeting_service, "ParseCache", side_effect=parse_cache.ParseCacheUnavailable):
            uncached_reminders = due_reminder_meetings(records, now=reminder_now)
        self.assertEqual(cached_reminders, uncached_reminders)
        self.assertEqual(len(cached_reminders), 1)

    def test_second_call_skips_extract_meetings(self):
        records = cache_records()
        expected = build_digest_result(records, "weekly", target_date=date(2026, 11, 23))

        with patch.object(meeting_service, "extract_meetings", side_effect=AssertionError("cache miss")):
            actual = build_digest_result(records, "weekly", target_date=date(2026, 11, 23))

        self.assertEqual(actual, expected)

    def test_only_one_sqlite_connection_is_opened_per_collection(self):
        with patch.object(parse_cache.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            build_digest_result(cache_records(), "daily", target_date=date(2026, 11, 23))

        self.assertEqual(connect.call_count, 1)

    def test_content_change_and_parser_version_change_invalidate_cache(self):
        record = cache_records()[-1]
        target = date(2026, 11, 23)
        build_digest_result([record], "daily", target_date=target)

        changed_record = {**record, "content": record["content"] + " Fixture revision."}
        with patch.object(meeting_service, "extract_meetings", wraps=extract_meetings) as extract:
            build_digest_result([changed_record], "daily", target_date=target)
        self.assertEqual(extract.call_count, 1)

        with patch.object(parse_cache, "PARSER_VERSION", parse_cache.PARSER_VERSION + 1):
            with patch.object(meeting_service, "extract_meetings", wraps=extract_meetings) as extract:
                build_digest_result([changed_record], "daily", target_date=target)
        self.assertEqual(extract.call_count, 1)

    def test_corrupt_cache_falls_back_to_normal_parsing(self):
        self.cache_path.write_bytes(b"not a sqlite database")

        with self.assertLogs("mail_digest.services.meeting_service", level="WARNING"):
            result = build_digest_result(
                [cache_records()[-1]], "daily", target_date=date(2026, 11, 23)
            )

        self.assertEqual(result.count, 1)

    def test_unreadable_cache_falls_back_to_normal_parsing(self):
        with (
            patch.object(
                parse_cache.sqlite3,
                "connect",
                side_effect=sqlite3.OperationalError("unavailable"),
            ),
            self.assertLogs("mail_digest.services.meeting_service", level="WARNING"),
        ):
            result = build_digest_result(
                [cache_records()[-1]], "daily", target_date=date(2026, 11, 23)
            )

        self.assertEqual(result.count, 1)

    def test_old_range_entries_are_removed_on_each_pass(self):
        today = local_now().date()
        with parse_cache.ParseCache(self.cache_path) as cache:
            cache.connection.executemany(
                "INSERT INTO parse_results(cache_key, start_date, result_json) VALUES (?, ?, ?)",
                [
                    ("stale", (today - timedelta(days=3)).isoformat(), "[]"),
                    ("recent", (today - timedelta(days=1)).isoformat(), "[]"),
                ],
            )
            cache.connection.commit()

        with parse_cache.ParseCache(self.cache_path) as cache:
            cache.store(today, {})
            rows = cache.connection.execute(
                "SELECT cache_key FROM parse_results ORDER BY cache_key"
            ).fetchall()

        self.assertEqual([row[0] for row in rows], ["recent"])

    def test_cache_file_and_directory_permissions_are_private(self):
        build_digest_result(
            [cache_records()[-1]], "daily", target_date=date(2026, 11, 23)
        )

        self.assertEqual(stat.S_IMODE(self.cache_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.cache_path.parent.stat().st_mode), 0o700)

    def test_json_codec_preserves_dates_aware_timezone_and_timedelta(self):
        value = {
            "day": date(2026, 11, 23),
            "instant": datetime(2026, 11, 23, 10, 0, tzinfo=ZoneInfo("Europe/Istanbul")),
            "duration": timedelta(days=1, seconds=2, microseconds=3),
        }

        restored = parse_cache._decode(parse_cache._encode(value))

        self.assertEqual(restored, value)
        self.assertEqual(restored["instant"].tzinfo.key, "Europe/Istanbul")


if __name__ == "__main__":
    unittest.main()
