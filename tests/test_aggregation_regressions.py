import unittest
from datetime import date

from main import format_digest, format_upcoming_digest, parse_ics_meetings


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

    def test_equal_sequence_cancel_wins_over_duplicate_confirmation(self):
        confirmed = ics_record(sequence=3, start="20260815T100000")
        cancelled = ics_record(sequence=3, start="20260815T100000", status="CANCELLED")

        self.assertEqual(parse_ics_meetings(confirmed)[0].status, "CONFIRMED")
        digest = format_upcoming_digest([confirmed, cancelled], date(2026, 8, 14))

        self.assertIn("Bugün veya sonrasında toplantı yok.", digest)

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
