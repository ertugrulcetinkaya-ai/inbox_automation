import unittest
from datetime import date, datetime

from mail_digest.parsing.ics import parse_ics_meetings
from mail_digest.services.meeting_service import (
    build_digest_result,
    due_reminder_meetings,
)
from parse_cache_test_support import isolate_parse_cache

isolate_parse_cache()


def event(
    uid="synthetic-series",
    start="DTSTART;TZID=Europe/Istanbul:20261106T100000",
    sequence=0,
    title="Synthetic recurring meeting",
    recurrence=None,
    recurrence_id=None,
    status=None,
    extra=(),
):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SEQUENCE:{sequence}", start]
    if recurrence_id:
        lines.append(recurrence_id)
    if recurrence:
        lines.append(recurrence)
    lines.extend(extra)
    lines.append(f"SUMMARY:{title}")
    if status:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return lines


def record(*events, method=None, uid="synthetic-series"):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    if method:
        lines.append(f"METHOD:{method}")
    for event_lines in events:
        lines.extend(event_lines)
    lines.append("END:VCALENDAR")
    return {
        "source_message_id": f"{uid}@example.test",
        "received_date": "2026-11-06T09:00:00",
        "sender": "Synthetic Organizer <organizer@example.test>",
        "subject": "Synthetic calendar event",
        "content": "\n".join(lines),
    }


def friday_series(**kwargs):
    return record(
        event(recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR", **kwargs),
        uid=kwargs.get("uid", "synthetic-series"),
    )


class ICSRecurrenceTests(unittest.TestCase):
    def test_weekly_friday_master_renders_on_later_target_day(self):
        recurring = friday_series()

        result = build_digest_result(
            [recurring],
            "daily",
            target_date=date(2026, 11, 20),
        )

        self.assertEqual(result.count, 1)
        self.assertIn("10:00", result.message)
        self.assertIn("Synthetic recurring meeting", result.message)

    def test_exdate_removes_only_the_excluded_occurrence(self):
        recurring = record(
            event(
                recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR",
                extra=("EXDATE;TZID=Europe/Istanbul:20261120T100000",),
            )
        )

        excluded = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )
        included = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 27)
        )

        self.assertEqual(excluded.count, 0)
        self.assertEqual(included.count, 1)

    def test_rdate_adds_an_occurrence_outside_the_rule(self):
        recurring = record(
            event(
                recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=2",
                extra=("RDATE;TZID=Europe/Istanbul:20261125T100000",),
            )
        )

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 25)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("10:00", result.message)

    def test_rdate_only_series_expands_without_rrule(self):
        recurring = record(
            event(
                extra=(
                    "RDATE;TZID=Europe/Istanbul:20261113T100000,20261120T100000",
                )
            )
        )

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("10:00", result.message)

    def test_single_occurrence_update_replaces_the_master_occurrence(self):
        master = event(recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR")
        override = event(
            sequence=1,
            start="DTSTART;TZID=Europe/Istanbul:20261120T110000",
            recurrence_id="RECURRENCE-ID;TZID=Europe/Istanbul:20261120T100000",
        )
        recurring = record(master, override)

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("11:00", result.message)
        self.assertNotIn("10:00", result.message)

    def _berlin_series(self):
        return event(
            start="DTSTART;TZID=Europe/Berlin:20261106T090000",
            recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR",
        )

    def _berlin_override(self, recurrence_id):
        return event(
            sequence=1,
            start="DTSTART;TZID=Europe/Istanbul:20261120T150000",
            recurrence_id=recurrence_id,
        )

    def test_utc_recurrence_id_replaces_occurrence_in_same_message(self):
        recurring = record(
            self._berlin_series(),
            self._berlin_override("RECURRENCE-ID:20261120T080000Z"),
        )

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("15:00", result.message)
        self.assertNotIn("11:00", result.message)

    def test_utc_recurrence_id_replaces_occurrence_across_messages(self):
        master = record(self._berlin_series())
        master["source_message_id"] = "synthetic-master@example.test"
        update = record(
            self._berlin_override("RECURRENCE-ID:20261120T080000Z")
        )
        update["source_message_id"] = "synthetic-update@example.test"

        result = build_digest_result(
            [master, update], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("15:00", result.message)
        self.assertNotIn("11:00", result.message)

    def test_recurrence_id_in_other_timezone_matches_same_instant(self):
        recurring = record(
            self._berlin_series(),
            self._berlin_override(
                "RECURRENCE-ID;TZID=America/New_York:20261120T030000"
            ),
        )

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("15:00", result.message)
        self.assertNotIn("11:00", result.message)

    def test_all_day_occurrence_update_uses_date_identity(self):
        recurring = record(
            event(
                start="DTSTART;VALUE=DATE:20261106",
                recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR",
            ),
            event(
                sequence=1,
                start="DTSTART;VALUE=DATE:20261120",
                recurrence_id="RECURRENCE-ID;VALUE=DATE:20261120",
                title="Updated all-day meeting",
            ),
        )

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("Updated all-day meeting", result.message)
        self.assertNotIn("Synthetic recurring meeting", result.message)

    def test_upcoming_collapses_daily_series_and_marks_the_row(self):
        recurring = record(
            event(
                start="DTSTART;TZID=Europe/Istanbul:20261121T100000",
                recurrence="RRULE:FREQ=DAILY;COUNT=4",
            )
        )

        result = build_digest_result(
            [recurring], "upcoming", target_date=date(2026, 11, 21)
        )

        self.assertEqual(result.count, 1)
        self.assertEqual(result.message.count("• "), 1)
        self.assertIn("🔁 Tekrarlayan", result.message)

    def test_weekly_keeps_each_daily_series_occurrence(self):
        recurring = record(
            event(
                start="DTSTART;TZID=Europe/Istanbul:20261121T100000",
                recurrence="RRULE:FREQ=DAILY;COUNT=3",
            )
        )

        result = build_digest_result(
            [recurring], "weekly", target_date=date(2026, 11, 21)
        )

        self.assertEqual(result.count, 3)
        self.assertEqual(result.message.count("• "), 3)
        self.assertNotIn("🔁 Tekrarlayan", result.message)

    def test_attention_collapses_and_marks_recurring_occurrence(self):
        recurring = record(
            event(
                start="DTSTART;TZID=Europe/Istanbul:20261121T100000",
                recurrence="RRULE:FREQ=DAILY;COUNT=4",
                status="TENTATIVE",
            )
        )

        result = build_digest_result(
            [recurring], "attention", target_date=date(2026, 11, 21)
        )

        self.assertEqual(result.count, 1)
        self.assertEqual(result.message.count("• "), 1)
        self.assertIn("🔁 Tekrarlayan", result.message)

    def test_nearest_shifted_occurrence_is_the_upcoming_series_row(self):
        recurring = record(
            event(
                start="DTSTART;TZID=Europe/Istanbul:20261127T100000",
                recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR",
            ),
            event(
                sequence=1,
                start="DTSTART;TZID=Europe/Istanbul:20261122T080000",
                recurrence_id=(
                    "RECURRENCE-ID;TZID=Europe/Istanbul:20261204T100000"
                ),
                title="Shifted closest occurrence",
            ),
        )

        result = build_digest_result(
            [recurring], "upcoming", target_date=date(2026, 11, 21)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("Shifted closest occurrence", result.message)
        self.assertIn("08:00", result.message)
        self.assertIn("22 Kasım 2026", result.message)
        self.assertNotIn("27 Kasım 2026", result.message)

    def test_series_cancellation_suppresses_all_occurrences(self):
        master = friday_series()
        for sequence in (0, 1):
            for cancellation_kwargs in (
                {"method": "CANCEL"},
                {"method": None, "status": "CANCELLED"},
            ):
                with self.subTest(sequence=sequence, cancellation=cancellation_kwargs):
                    cancellation_event = event(
                        sequence=sequence,
                        recurrence=None,
                        status=cancellation_kwargs.get("status"),
                    )
                    cancellation = record(
                        cancellation_event,
                        method=cancellation_kwargs.get("method"),
                    )
                    result = build_digest_result(
                        [master, cancellation],
                        "daily",
                        target_date=date(2026, 11, 20),
                    )
                    self.assertEqual(result.count, 0)

    def test_single_occurrence_cancellation_keeps_other_dates(self):
        master = friday_series()
        cancellation_event = event(
            sequence=1,
            start="DTSTART;TZID=Europe/Istanbul:20261120T100000",
            recurrence_id="RECURRENCE-ID;TZID=Europe/Istanbul:20261120T100000",
            status="CANCELLED",
        )
        cancellation = record(cancellation_event)

        cancelled_day = build_digest_result(
            [master, cancellation], "daily", target_date=date(2026, 11, 20)
        )
        next_day = build_digest_result(
            [master, cancellation], "daily", target_date=date(2026, 11, 27)
        )

        self.assertEqual(cancelled_day.count, 0)
        self.assertEqual(next_day.count, 1)

    def test_count_and_utc_until_bound_series(self):
        count_limited = record(
            event(recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=3")
        )
        until_limited = record(
            event(recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20261120T080000Z")
        )

        for recurring in (count_limited, until_limited):
            meetings = parse_ics_meetings(
                recurring,
                range_start=date(2026, 11, 6),
                range_end=date(2026, 11, 27),
            )
            self.assertEqual(
                [meeting.start_at.date() for meeting in meetings],
                [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20)],
            )

    def test_series_keeps_event_timezone_across_berlin_dst(self):
        recurring = record(
            event(
                start="DTSTART;TZID=Europe/Berlin:20261016T100000",
                recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=3",
            )
        )

        before = build_digest_result(
            [recurring], "daily", target_date=date(2026, 10, 23)
        )
        after = build_digest_result(
            [recurring], "daily", target_date=date(2026, 10, 30)
        )

        self.assertIn("11:00", before.message)
        self.assertIn("12:00", after.message)

    def test_all_day_series_expands_as_all_day(self):
        recurring = record(
            event(
                start="DTSTART;VALUE=DATE:20261106",
                recurrence="RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=3",
            )
        )

        result = build_digest_result(
            [recurring], "daily", target_date=date(2026, 11, 20)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("Tüm gün", result.message)

    def test_reminder_selects_a_recurring_occurrence(self):
        recurring = friday_series()

        due = due_reminder_meetings(
            [recurring],
            now=datetime(2026, 11, 20, 9, 45),
            lead_minutes=15,
            window_minutes=5,
        )

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["date"], date(2026, 11, 20))
        self.assertEqual(due[0]["time"], "10:00")

    def test_recurring_expansion_is_capped_at_500_occurrences(self):
        recurring = record(
            event(
                start="DTSTART;TZID=Europe/Istanbul:20261106T100000",
                recurrence="RRULE:FREQ=MINUTELY;COUNT=1000",
            )
        )

        meetings = parse_ics_meetings(
            recurring,
            range_start=date(2026, 11, 6),
            range_end=date(2026, 11, 6),
        )

        self.assertEqual(len(meetings), 500)


if __name__ == "__main__":
    unittest.main()
