import unittest
from datetime import date

from main import extract_meeting, format_digest, parse_received_date


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


if __name__ == "__main__":
    unittest.main()
