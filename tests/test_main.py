import unittest
from datetime import date

from main import extract_meeting, format_digest, format_upcoming_digest, parse_received_date


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


if __name__ == "__main__":
    unittest.main()
