import unittest
from datetime import date

from main import (
    extract_meeting,
    format_digest,
    format_upcoming_digest,
    parse_ics_meetings,
    parse_received_date,
)


class MeetingDateContextTests(unittest.TestCase):
    def setUp(self):
        self.received_date = date(2026, 8, 7)
        self.today = date(2026, 8, 14)
        self.record = {
            "sender": "Sinem Arın <sarin@ford.com.tr>",
            "subject": "Satış Sonrası Bilgilendirme Webinarı - Ağustos",
            "date": "Friday, August 7, 2026 at 09:00:00",
            "content": (
                "Bugün gerçekleştireceğimiz Ağustos ayı Satış Sonrası "
                "Webinarı sunumunu ekte bilgilerinize sunarım."
            ),
        }

    def test_received_date_parser_handles_apple_mail_english_date(self):
        self.assertEqual(parse_received_date(self.record["date"]), self.received_date)

    def test_relative_today_is_anchored_to_received_date(self):
        self.assertIsNone(extract_meeting(self.record, self.today))
        self.assertIsNotNone(extract_meeting(self.record, self.received_date))

    def test_digest_does_not_list_old_relative_today_message(self):
        digest = format_digest([self.record], self.today)
        self.assertIn("Bugün toplantı yok.", digest)
        self.assertNotIn("Satış Sonrası Bilgilendirme Webinarı", digest)

    def test_upcoming_digest_includes_future_explicit_date(self):
        future_record = {
            **self.record,
            "subject": "Gelecek Webinar Toplantısı",
            "content": "15 Ağustos 2026 tarihinde saat 10:00'da yapılacaktır.",
        }
        digest = format_upcoming_digest([self.record, future_record], self.today)
        self.assertIn("15 Ağustos 2026", digest)
        self.assertIn("Gelecek Webinar Toplantısı", digest)
        self.assertNotIn("Satış Sonrası Bilgilendirme Webinarı", digest)

    def test_dotted_time_is_not_also_parsed_as_october_thirtieth(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Bugünkü toplantı",
            "content": "Toplantımız bugün saat 10.30'da yapılacaktır.",
        }

        meeting = extract_meeting(record, self.today)

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["date"], self.today)
        self.assertEqual(meeting["time"], "10:30")

    def test_dotted_time_range_stays_on_the_time_side(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Bugünkü toplantı",
            "content": "Toplantı bugün 10.30 – 11.30 arasında yapılacaktır.",
        }

        meeting = extract_meeting(record, self.today)

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["date"], self.today)
        self.assertEqual(meeting["time"], "10:30–11:30")

    def test_dotted_day_month_is_parsed_as_date(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Ağustos toplantısı",
            "content": "Toplantı 15.08 tarihinde saat 09.00'da yapılacaktır.",
        }

        meeting = extract_meeting(record, date(2026, 8, 15))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["date"], date(2026, 8, 15))
        self.assertEqual(meeting["time"], "09:00")

    def test_dotted_day_month_with_year_is_parsed_as_date(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Ağustos toplantısı",
            "content": "Toplantı 15.08.2026 tarihinde saat 09:00'da yapılacaktır.",
        }

        meeting = extract_meeting(record, date(2026, 8, 15))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["date"], date(2026, 8, 15))
        self.assertEqual(meeting["time"], "09:00")

    def test_ambiguous_dotted_number_needs_date_context(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Tarih bilgisi",
            "content": "Toplantı 10.08 tarihinde saat 09.00'da yapılacaktır.",
        }

        meeting = extract_meeting(record, date(2026, 8, 10))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["date"], date(2026, 8, 10))
        self.assertEqual(meeting["time"], "09:00")

    def test_yearless_january_date_rolls_into_next_year(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Ocak toplantısı",
            "content": "Toplantı 5 Ocak saat 10:00'da yapılacaktır.",
        }

        digest = format_upcoming_digest([record], date(2026, 12, 20))

        self.assertIn("5 Ocak 2027", digest)
        self.assertNotIn("5 Ocak 2026", digest)

    def test_recent_yearless_past_date_does_not_roll_into_next_year(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Ağustos toplantısı",
            "content": "Toplantı 5 Ağustos saat 10:00'da yapılacaktır.",
        }

        meeting = extract_meeting(record, date(2026, 8, 14))

        self.assertIsNone(meeting)


class ICSMeetingTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "source_message_id": "<calendar-123@example.com>",
            "sender": "Calendar Service <calendar@example.com>",
            "subject": "Ford Otosan Satış Sonrası Ağustos Değerlendirmesi",
            "content": (
                "BEGIN:VCALENDAR\n"
                "VERSION:2.0\n"
                "BEGIN:VEVENT\n"
                "UID:ics-123@example.com\n"
                "DTSTART;TZID=Europe/Istanbul:20260815T100000\n"
                "DTEND;TZID=Europe/Istanbul:20260815T103000\n"
                "SUMMARY:Ford Otosan Satış Sonrası Ağustos \n"
                " Değerlendirmesi\n"
                "ORGANIZER;CN=Satış Sonrası Ekibi:mailto:after-sales@example.com\n"
                "LOCATION:Toplantı Odası A\n"
                "DESCRIPTION:Microsoft Teams toplantısına katılın\\n"
                " https://teams.microsoft.com/l/meetup-join/abc123\n"
                "STATUS:CONFIRMED\n"
                "END:VEVENT\n"
                "END:VCALENDAR\n"
            ),
        }

    def test_ics_is_parsed_before_subject_semantics(self):
        meeting = extract_meeting(self.record, date(2026, 8, 15))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["uid"], "ics-123@example.com")
        self.assertEqual(meeting["subject"], "Ford Otosan Satış Sonrası Ağustos Değerlendirmesi")
        self.assertEqual(meeting["sender"], "Satış Sonrası Ekibi")
        self.assertEqual(meeting["date"], date(2026, 8, 15))
        self.assertEqual(meeting["time"], "10:00–10:30")
        self.assertEqual(meeting["location"], "Toplantı Odası A")
        self.assertEqual(meeting["join_url"], "https://teams.microsoft.com/l/meetup-join/abc123")
        self.assertEqual(meeting["confidence"], 1.0)

    def test_mime_ics_attachment_is_parsed_from_raw_source(self):
        raw_source = (
            "Content-Type: multipart/mixed; boundary=cal-boundary\n"
            "\n"
            "--cal-boundary\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            "Toplantı davetiyesi\n"
            "--cal-boundary\n"
            "Content-Type: text/calendar; charset=utf-8; method=REQUEST\n"
            "Content-Disposition: attachment; filename=invite.ics\n"
            "\n"
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:attachment-123@example.com\n"
            "DTSTART:20260816T070000Z\n"
            "SUMMARY:Ekli Takvim Daveti\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
            "--cal-boundary--\n"
        )
        record = {
            "source_message_id": "<raw-123@example.com>",
            "sender": "Calendar Service <calendar@example.com>",
            "subject": "Ford Otosan Satış Sonrası Ağustos Değerlendirmesi",
            "content": "Toplantı davetiyesi",
            "raw_source": raw_source,
        }

        meetings = parse_ics_meetings(record)

        self.assertIsNotNone(meetings)
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].uid, "attachment-123@example.com")
        self.assertEqual(meetings[0].start_at.date(), date(2026, 8, 16))

    def test_cancelled_ics_event_is_not_returned(self):
        record = {
            **self.record,
            "content": self.record["content"].replace("STATUS:CONFIRMED", "STATUS:CANCELLED"),
        }

        self.assertIsNone(extract_meeting(record, date(2026, 8, 15)))

    def test_ics_method_cancel_is_normalized_to_cancelled(self):
        record = {
            **self.record,
            "content": self.record["content"].replace(
                "VERSION:2.0\n",
                "VERSION:2.0\nMETHOD:CANCEL\n",
            ).replace("STATUS:CONFIRMED\n", "SEQUENCE:1\n"),
        }

        meetings = parse_ics_meetings(record)

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].status, "CANCELLED")
        self.assertEqual(meetings[0].sequence, 1)

    def test_ics_sequence_prefers_newer_rescheduled_event(self):
        updated = {
            **self.record,
            "content": self.record["content"]
            .replace("VERSION:2.0\n", "VERSION:2.0\n")
            .replace("DTSTART;TZID=Europe/Istanbul:20260815T100000", "DTSTART;TZID=Europe/Istanbul:20260818T140000")
            .replace("DTEND;TZID=Europe/Istanbul:20260815T103000", "DTEND;TZID=Europe/Istanbul:20260818T143000")
            .replace("STATUS:CONFIRMED", "SEQUENCE:1\nSTATUS:CONFIRMED"),
        }

        digest = format_upcoming_digest([self.record, updated], date(2026, 8, 14))

        self.assertIn("18 Ağustos 2026", digest)
        self.assertNotIn("15 Ağustos 2026", digest)

    def test_ics_sequence_cancellation_suppresses_previous_event(self):
        cancelled = {
            **self.record,
            "content": self.record["content"].replace(
                "VERSION:2.0\n",
                "VERSION:2.0\nMETHOD:CANCEL\n",
            ).replace("STATUS:CONFIRMED", "SEQUENCE:1"),
        }

        digest = format_upcoming_digest([self.record, cancelled], date(2026, 8, 14))

        self.assertIn("Bugün veya sonrasında toplantı yok.", digest)

    def test_semantic_cancellation_is_not_listed(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Toplantı iptali",
            "content": "15 Ağustos 2026 saat 10:00 toplantısı iptal edilmiştir.",
        }

        self.assertIsNone(extract_meeting(record, date(2026, 8, 15)))

    def test_semantic_reschedule_keeps_only_new_date(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Satış toplantısı",
            "content": "15 Ağustos toplantımız 18 Ağustos 14:00'e ertelenmiştir.",
        }

        self.assertIsNone(extract_meeting(record, date(2026, 8, 15)))
        meeting = extract_meeting(record, date(2026, 8, 18))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["status"], "RESCHEDULED")
        self.assertEqual(meeting["date"], date(2026, 8, 18))
        self.assertEqual(meeting["time"], "14:00")

    def test_semantic_tentative_meeting_keeps_status(self):
        record = {
            "sender": "Toplantı Organizatörü <meetings@example.com>",
            "subject": "Taslak toplantı",
            "content": "15 Ağustos 2026 saat 10:00 için tentative toplantı.",
        }

        meeting = extract_meeting(record, date(2026, 8, 15))
        digest = format_digest([record], date(2026, 8, 15))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["status"], "TENTATIVE")
        self.assertIn("[Kesinleşmedi]", digest)

    def test_semantic_fallback_can_use_body_when_subject_is_neutral(self):
        record = {
            "sender": "Satış Sonrası Ekibi <after-sales@example.com>",
            "subject": "Ford Otosan Satış Sonrası Ağustos Değerlendirmesi",
            "content": (
                "Microsoft Teams toplantısına katılın. "
                "15 Ağustos 2026 saat 10:00'da görüşeceğiz."
            ),
        }

        meeting = extract_meeting(record, date(2026, 8, 15))

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting["date"], date(2026, 8, 15))
        self.assertEqual(meeting["time"], "10:00")
        self.assertEqual(meeting["confidence"], 0.55)


if __name__ == "__main__":
    unittest.main()
