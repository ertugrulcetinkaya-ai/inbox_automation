"""Synthetic regressions for common iCalendar syntax variants."""

from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

import mail_digest.parsing.ics as ics
from mail_digest.parsing.ics import parse_ics_meetings
from mail_digest.services.meeting_service import build_digest_result
from parse_cache_test_support import isolate_parse_cache

isolate_parse_cache()


def calendar_record(event_lines, *, prefix=()):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", *prefix, "BEGIN:VEVENT"]
    lines.extend(event_lines)
    lines.extend(("END:VEVENT", "END:VCALENDAR"))
    return {
        "source_message_id": "ics-syntax-fixture@example.com",
        "received_date": "2026-11-21T09:00:00",
        "source_received_at": datetime(2026, 11, 21, 9, 0),
        "sender": "Synthetic Organizer <fixture@example.com>",
        "subject": "Synthetic calendar event",
        "content": "\n".join(lines),
    }


class ICSSyntaxTests(unittest.TestCase):
    def test_windows_eastern_timezone_is_converted_to_istanbul_time(self):
        record = calendar_record(
            (
                "UID:windows-timezone",
                "DTSTART;TZID=Eastern Standard Time:20261122T100000",
                "SUMMARY:Windows timezone meeting",
            )
        )

        meetings = parse_ics_meetings(record)
        result = build_digest_result([record], "daily", target_date=date(2026, 11, 22))

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].start_at.utcoffset(), timedelta(hours=-5))
        self.assertIn("18:00", result.message)

    def test_unknown_timezone_uses_vtimezone_standard_offset(self):
        record = calendar_record(
            (
                "UID:custom-timezone",
                "DTSTART;TZID=Custom/Fixture:20261122T100000",
                "SUMMARY:Custom timezone meeting",
            ),
            prefix=(
                "BEGIN:VTIMEZONE",
                "TZID:Custom/Fixture",
                "BEGIN:STANDARD",
                "DTSTART:19700101T000000",
                "TZOFFSETFROM:+0200",
                "TZOFFSETTO:+0330",
                "END:STANDARD",
                "END:VTIMEZONE",
            ),
        )

        meetings = parse_ics_meetings(record)
        result = build_digest_result([record], "daily", target_date=date(2026, 11, 22))

        self.assertEqual(meetings[0].start_at.utcoffset(), timedelta(hours=3, minutes=30))
        self.assertIn("09:30", result.message)

    def test_unresolved_timezone_keeps_naive_value_and_increments_counter(self):
        record = calendar_record(
            (
                "UID:unresolved-timezone",
                "DTSTART;TZID=Unknown/Fixture:20261122T100000",
                "SUMMARY:Unresolved timezone meeting",
            )
        )

        with patch.object(ics, "UNRESOLVED_TZID_COUNT", 0):
            meetings = parse_ics_meetings(record)

            self.assertIsNone(meetings[0].start_at.tzinfo)
            self.assertEqual(ics.UNRESOLVED_TZID_COUNT, 1)

    def test_valarm_properties_do_not_override_vevent(self):
        record = calendar_record(
            (
                "UID:alarm-isolation",
                "DTSTART;TZID=Europe/Istanbul:20261122T100000",
                "BEGIN:VALARM",
                "STATUS:CANCELLED",
                "SUMMARY:Alarm title must be ignored",
                "END:VALARM",
                "SUMMARY:Actual meeting title",
            )
        )

        result = build_digest_result([record], "daily", target_date=date(2026, 11, 22))

        self.assertEqual(result.count, 1)
        self.assertIn("Actual meeting title", result.message)
        self.assertNotIn("Alarm title must be ignored", result.message)

    def test_duration_hour_minute_form_sets_missing_end(self):
        record = calendar_record(
            (
                "UID:duration-time",
                "DTSTART;TZID=Europe/Istanbul:20261122T100000",
                "DURATION:PT1H30M",
                "SUMMARY:Duration meeting",
            )
        )

        meeting = parse_ics_meetings(record)[0]

        self.assertEqual(meeting.end_at, datetime(2026, 11, 22, 11, 30, tzinfo=meeting.start_at.tzinfo))

    def test_duration_week_form_sets_all_day_end(self):
        record = calendar_record(
            (
                "UID:duration-weeks",
                "DTSTART;VALUE=DATE:20261122",
                "DURATION:P2W",
                "SUMMARY:Two week all day event",
            )
        )

        meeting = parse_ics_meetings(record)[0]

        self.assertEqual(meeting.start_at, date(2026, 11, 22))
        self.assertEqual(meeting.end_at, date(2026, 12, 6))

    def test_quoted_timezone_parameter_can_contain_colon(self):
        record = calendar_record(
            (
                "UID:quoted-timezone",
                'DTSTART;TZID="UTC+03:00":20261122T100000',
                "SUMMARY:Quoted timezone meeting",
            )
        )

        meeting = parse_ics_meetings(record)[0]

        self.assertEqual(meeting.start_at.utcoffset(), timedelta(hours=3))
        self.assertEqual(meeting.timezone, "UTC+03:00")


if __name__ == "__main__":
    unittest.main()
