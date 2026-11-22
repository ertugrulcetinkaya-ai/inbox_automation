import unittest
from datetime import date, datetime

from main import format_digest, format_upcoming_digest, parse_ics_meetings
from mail_digest.services.meeting_service import build_digest_result
from parse_cache_test_support import isolate_parse_cache

isolate_parse_cache()


def ics_record(uid="same-meeting@example.com", sequence=0, start="20260815T100000", status="CONFIRMED"):
    return {
        "source_message_id": f"<{uid}-{sequence}@example.com>",
        "sender": "Calendar Fixture <calendar@example.com>",
        "subject": "Fixture toplantısı",
        "content": (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "BEGIN:VEVENT\n"
            f"UID:{uid}\n"
            f"SEQUENCE:{sequence}\n"
            f"DTSTART;TZID=Europe/Istanbul:{start}\n"
            "SUMMARY:Aynı toplantı\n"
            f"STATUS:{status}\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        ),
    }


class MeetingAggregationRegressionTests(unittest.TestCase):
    def test_duplicate_invite_is_listed_once(self):
        record = ics_record()

        digest = format_upcoming_digest([record, {**record}], date(2026, 8, 14))

        self.assertEqual(digest.count("Aynı toplantı"), 1)

    def test_three_updates_keep_only_highest_sequence(self):
        records = [
            ics_record(sequence=0, start="20260815T100000"),
            ics_record(sequence=2, start="20260820T160000"),
            ics_record(sequence=1, start="20260818T140000"),
        ]

        digest = format_upcoming_digest(records, date(2026, 8, 14))

        self.assertIn("20 Ağustos 2026", digest)
        self.assertNotIn("15 Ağustos 2026", digest)
        self.assertNotIn("18 Ağustos 2026", digest)

    def test_scoped_digest_does_not_keep_old_date_when_new_sequence_moves_outside_range(self):
        records = [
            ics_record(sequence=0, start="20260815T100000"),
            ics_record(sequence=1, start="20260820T100000"),
        ]

        daily = format_digest(records, date(2026, 8, 15))

        self.assertIn("Bugün toplantı yok.", daily)
        self.assertNotIn("Aynı toplantı", daily)

    def test_scoped_digest_does_not_keep_old_semantic_date_after_reschedule(self):
        original = {
            "sender": "Fixture Organizer <organizer@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Ağustos 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 8, 14, 9, 0),
        }
        rescheduled = {
            **original,
            "content": "15 Ağustos toplantımız 20 Ağustos 14:00'e ertelendi.",
            "source_received_at": datetime(2026, 8, 14, 10, 0),
        }

        daily = format_digest([original, rescheduled], date(2026, 8, 15))

        self.assertIn("Bugün toplantı yok.", daily)
        self.assertNotIn("Satış toplantısı", daily)

    def test_equal_sequence_cancel_wins_over_duplicate_confirmation(self):
        confirmed = ics_record(sequence=3, start="20260815T100000")
        cancelled = ics_record(sequence=3, start="20260815T100000", status="CANCELLED")
        confirmed["source_received_at"] = datetime(2026, 8, 14, 10, 0)
        cancelled["source_received_at"] = datetime(2026, 8, 14, 9, 0)

        self.assertEqual(parse_ics_meetings(confirmed)[0].status, "CONFIRMED")
        digest = format_upcoming_digest([confirmed, cancelled], date(2026, 8, 14))

        self.assertIn("Bugün veya sonrasında toplantı yok.", digest)

    def test_equal_sequence_newer_confirmation_beats_older_tentative(self):
        older_tentative = ics_record(
            uid="same-sequence-status",
            sequence=2,
            start="20260920T100000",
            status="TENTATIVE",
        )
        newer_confirmed = ics_record(
            uid="same-sequence-status",
            sequence=2,
            start="20260920T100000",
            status="CONFIRMED",
        )
        older_tentative["source_received_at"] = datetime(2026, 9, 12, 9, 0)
        newer_confirmed["source_received_at"] = datetime(2026, 9, 12, 10, 0)

        result = build_digest_result(
            [older_tentative, newer_confirmed],
            "daily",
            target_date=date(2026, 9, 20),
        )

        self.assertEqual(result.count, 1)
        self.assertEqual(result.meetings[0]["status"], "CONFIRMED")
        self.assertNotIn("Kesinleşmedi", result.message)

    def test_semantic_lifecycle_uses_source_freshness_before_status_priority(self):
        older_tentative = {
            "sender": "Fixture Organizer <fixture@example.com>",
            "subject": "Status meeting",
            "content": "20 Eylül 2026 saat 10:00 için taslak toplantı.",
            "source_received_at": datetime(2026, 9, 12, 9, 0),
        }
        newer_confirmed = {
            "sender": "Fixture Organizer <fixture@example.com>",
            "subject": "Status meeting",
            "content": "20 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 9, 12, 10, 0),
        }

        result = build_digest_result(
            [older_tentative, newer_confirmed],
            "daily",
            target_date=date(2026, 9, 20),
        )

        self.assertEqual(result.count, 1)
        self.assertEqual(result.meetings[0]["status"], "CONFIRMED")

    def test_cancellation_does_not_hide_different_uid_with_same_subject(self):
        cancelled = ics_record(
            uid="cancelled-instance",
            sequence=1,
            start="20260915T100000",
            status="CANCELLED",
        )
        valid = ics_record(
            uid="valid-instance",
            sequence=0,
            start="20260922T100000",
        )

        digest = format_upcoming_digest([cancelled, valid], date(2026, 9, 1))

        self.assertIn("22 Eylül 2026", digest)
        self.assertIn("Aynı toplantı", digest)

    def test_semantic_identity_keeps_same_subject_for_different_senders(self):
        records = [
            {
                "sender": "Birinci Organizatör <one@example.com>",
                "subject": "Proje toplantısı",
                "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            },
            {
                "sender": "İkinci Organizatör <two@example.com>",
                "subject": "Proje toplantısı",
                "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            },
        ]

        digest = format_upcoming_digest(records, date(2026, 9, 1))

        self.assertEqual(digest.count("Proje toplantısı"), 2)

    def test_date_only_semantic_cancellation_suppresses_original_timed_meeting(self):
        original = {
            "sender": "Organizer <organizer@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 9, 14, 9, 0),
        }
        cancellation = {
            "sender": "Different display name <organizer@example.com>",
            "subject": "Satış toplantısı iptali",
            "content": "15 Eylül 2026 tarihindeki toplantımız iptal edilmiştir.",
            "source_received_at": datetime(2026, 9, 14, 10, 0),
        }

        digest = format_upcoming_digest([original, cancellation], date(2026, 9, 1))

        self.assertIn("Bugün veya sonrasında toplantı yok.", digest)
        self.assertNotIn("Satış toplantısı", digest)

    def test_neutral_subject_date_only_cancellation_suppresses_original_timed_meeting(self):
        original = {
            "sender": "Organizer <organizer@example.com>",
            "subject": "Program değişikliği",
            "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 9, 14, 9, 0),
        }
        cancellation = {
            "sender": "Calendar Service <organizer@example.com>",
            "subject": "Program değişikliği",
            "content": "15 Eylül 2026 tarihindeki toplantımız iptal edilmiştir.",
            "source_received_at": datetime(2026, 9, 14, 10, 0),
        }

        digest = format_upcoming_digest([original, cancellation], date(2026, 9, 1))

        self.assertIn("Bugün veya sonrasında toplantı yok.", digest)
        self.assertNotIn("Program değişikliği", digest)

    def test_thread_cancellation_can_change_sender_and_subject(self):
        original = {
            "thread_id": "gmail-thread-1",
            "sender": "Organizer <organizer@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 9, 14, 9, 0),
        }
        cancellation = {
            "thread_id": "gmail-thread-1",
            "sender": "Calendar Service <calendar@example.com>",
            "subject": "Etkinlik iptal bildirimi",
            "content": "15 Eylül 2026 tarihindeki etkinlik iptal edilmiştir.",
            "source_received_at": datetime(2026, 9, 14, 10, 0),
        }

        digest = format_upcoming_digest([original, cancellation], date(2026, 9, 1))

        self.assertIn("Bugün veya sonrasında toplantı yok.", digest)
        self.assertNotIn("Satış toplantısı", digest)

    def test_not_newer_text_cancellation_does_not_suppress_meeting(self):
        original = {
            "sender": "Organizer <organizer@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 9, 14, 10, 0),
        }
        cancellation = {
            "sender": "Calendar Service <organizer@example.com>",
            "subject": "Satış toplantısı iptali",
            "content": "15 Eylül 2026 tarihindeki toplantımız iptal edilmiştir.",
            "source_received_at": datetime(2026, 9, 14, 9, 0),
        }

        digest = format_upcoming_digest([original, cancellation], date(2026, 9, 1))

        self.assertIn("15 Eylül 2026", digest)
        self.assertIn("Satış toplantısı", digest)

    def test_date_less_text_cancellation_does_not_use_received_date_as_target(self):
        original = {
            "thread_id": "gmail-thread-no-date",
            "sender": "Organizer <organizer@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
            "source_received_at": datetime(2026, 9, 15, 8, 0),
        }
        cancellation = {
            "thread_id": "gmail-thread-no-date",
            "sender": "Calendar Service <calendar@example.com>",
            "subject": "Toplantı iptal bildirimi",
            "content": "Toplantı iptal edilmiştir.",
            "received_date": date(2026, 9, 15),
            "source_received_at": datetime(2026, 9, 15, 9, 0),
        }

        digest = format_upcoming_digest([original, cancellation], date(2026, 9, 1))

        self.assertIn("15 Eylül 2026", digest)
        self.assertIn("Satış toplantısı", digest)

    def test_text_cancellation_does_not_suppress_uid_ics_event(self):
        invitation = ics_record(
            uid="uid-backed-invitation",
            sequence=0,
            start="20260915T100000",
        )
        invitation["thread_id"] = "gmail-thread-ics"
        invitation["source_received_at"] = datetime(2026, 9, 14, 9, 0)
        cancellation = {
            "thread_id": "gmail-thread-ics",
            "sender": "Calendar Service <calendar@example.com>",
            "subject": "Event cancellation",
            "content": "The 15 September 2026 meeting has been cancelled.",
            "source_received_at": datetime(2026, 9, 14, 10, 0),
        }

        digest = format_upcoming_digest([invitation, cancellation], date(2026, 9, 1))

        self.assertIn("15 Eylül 2026", digest)
        self.assertIn("Aynı toplantı", digest)

    def test_date_only_cancellation_from_different_sender_does_not_suppress(self):
        original = {
            "sender": "Organizer <one@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Eylül 2026 saat 10:00 toplantısı yapılacaktır.",
        }
        unrelated_cancellation = {
            "sender": "Another Organizer <two@example.com>",
            "subject": "Satış toplantısı iptali",
            "content": "15 Eylül 2026 tarihindeki toplantımız iptal edilmiştir.",
        }

        digest = format_upcoming_digest(
            [original, unrelated_cancellation],
            date(2026, 9, 1),
        )

        self.assertIn("15 Eylül 2026", digest)
        self.assertIn("Satış toplantısı", digest)

    def test_multiple_vcalendars_keep_method_scope_per_calendar(self):
        record = {
            "sender": "Calendar <calendar@example.com>",
            "subject": "Calendar update",
            "content": (
                "BEGIN:VCALENDAR\n"
                "METHOD:REQUEST\n"
                "BEGIN:VEVENT\n"
                "UID:request-event\n"
                "DTSTART;TZID=Europe/Istanbul:20260915T100000\n"
                "SUMMARY:İlk davet\n"
                "END:VEVENT\n"
                "END:VCALENDAR\n"
                "BEGIN:VCALENDAR\n"
                "METHOD:CANCEL\n"
                "BEGIN:VEVENT\n"
                "UID:cancel-event\n"
                "DTSTART;TZID=Europe/Istanbul:20260916T100000\n"
                "SUMMARY:İptal daveti\n"
                "END:VEVENT\n"
                "END:VCALENDAR\n"
            ),
        }

        meetings = parse_ics_meetings(record)

        self.assertEqual(
            {meeting.uid: meeting.status for meeting in meetings},
            {"request-event": "CONFIRMED", "cancel-event": "CANCELLED"},
        )

    def test_same_sequence_prefers_newer_source_timestamp(self):
        older = ics_record(
            uid="same-sequence",
            sequence=2,
            start="20260920T100000",
        )
        newer = ics_record(
            uid="same-sequence",
            sequence=2,
            start="20260921T100000",
        )
        older["source_received_at"] = datetime(2026, 9, 12, 9, 0)
        newer["source_received_at"] = datetime(2026, 9, 12, 10, 0)

        for records in ([older, newer], [newer, older]):
            with self.subTest(order=records):
                digest = format_upcoming_digest(records, date(2026, 9, 1))
                self.assertIn("21 Eylül 2026", digest)
                self.assertNotIn("20 Eylül 2026", digest)

    def test_recurring_occurrence_cancellation_does_not_hide_other_occurrences(self):
        record = {
            "sender": "Calendar <calendar@example.com>",
            "subject": "Seri toplantı",
            "content": (
                "BEGIN:VCALENDAR\n"
                "BEGIN:VEVENT\n"
                "UID:recurring-series\n"
                "RECURRENCE-ID;TZID=Europe/Istanbul:20260915T100000\n"
                "DTSTART;TZID=Europe/Istanbul:20260915T100000\n"
                "SUMMARY:Seri toplantı\n"
                "STATUS:CANCELLED\n"
                "END:VEVENT\n"
                "BEGIN:VEVENT\n"
                "UID:recurring-series\n"
                "RECURRENCE-ID;TZID=Europe/Istanbul:20260922T100000\n"
                "DTSTART;TZID=Europe/Istanbul:20260922T100000\n"
                "SUMMARY:Seri toplantı\n"
                "STATUS:CONFIRMED\n"
                "END:VEVENT\n"
                "END:VCALENDAR\n"
            ),
        }

        digest = format_upcoming_digest([record], date(2026, 9, 1))

        self.assertIn("22 Eylül 2026", digest)
        self.assertNotIn("15 Eylül 2026", digest)

    def test_semantic_duplicate_invite_is_listed_once(self):
        record = {
            "sender": "Fixture Organizer <organizer@example.com>",
            "subject": "Toplantı daveti",
            "content": "15 Ağustos 2026 saat 10:00 toplantısı yapılacaktır.",
        }

        digest = format_digest([record, {**record}], date(2026, 8, 15))

        self.assertEqual(digest.count("Toplantı daveti"), 1)

    def test_malformed_ics_payload_does_not_crash(self):
        record = {
            "subject": "Bozuk davet",
            "sender": "Fixture Organizer <organizer@example.com>",
            "content": "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Bozuk\nEND:VCALENDAR",
        }

        meetings = parse_ics_meetings(record)

        self.assertEqual(meetings, [])


if __name__ == "__main__":
    unittest.main()
