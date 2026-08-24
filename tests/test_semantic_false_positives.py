import unittest
from datetime import date

from main import extract_meetings
from mail_digest.services.meeting_service import format_attention_digest
from mail_digest.utils import strip_quoted_reply


def record(subject, content):
    return {
        "received_date": date(2026, 8, 24),
        "sender": "Fixture Sender <sender@example.com>",
        "subject": subject,
        "content": content,
    }


class SemanticFalsePositiveTests(unittest.TestCase):
    def test_visit_report_dates_are_not_meetings_when_only_body_mentions_a_meeting(self):
        candidate = record(
            "10.08.2026 Saha Ziyaret Raporu",
            (
                "Müşteri görüşme notları rapora eklenmiştir.\n"
                "Garanti bitiş tarihi: 15 Ekim 2026.\n"
                "Sonraki bakım dönemi: 5 Haziran 2027."
            ),
        )

        self.assertEqual(extract_meetings(candidate, date(2026, 8, 24)), [])

    def test_marketing_thread_dates_are_removed_with_the_quoted_header_block(self):
        content = (
            "Güncel pazarlama dosyası ektedir.\n\n"
            "From: Previous Sender <previous@example.com>\n"
            "Sent: Friday, July 3, 2026 16:34\n"
            "Subject: July marketing programs\n\n"
            "Webinar planlama tablosu 31 Ağustos 2026 ve 3 Nisan 2027."
        )
        candidate = record("RE: Temmuz 2026 Pazarlama Programları", content)

        self.assertEqual(strip_quoted_reply(content), "Güncel pazarlama dosyası ektedir.")
        self.assertEqual(extract_meetings(candidate, date(2026, 8, 24)), [])

    def test_planning_date_and_header_time_are_not_an_explicit_meeting(self):
        candidate = record(
            "Regülasyon Geçişi Hk.",
            (
                "Üretim planlama bilgileri güncellenmiştir. "
                "Geçiş hedefi 1 Nisan 2027 olarak kaydedildi. "
                "İleti kayıt saati 11:07."
            ),
        )

        self.assertEqual(extract_meetings(candidate, date(2026, 8, 24)), [])

    def test_body_only_candidate_passes_detailed_review_with_context_date_and_time(self):
        candidate = record(
            "Ağustos değerlendirmesi",
            "31 Ağustos 2026 saat 14:00'te Microsoft Teams toplantısında görüşeceğiz.",
        )

        meetings = extract_meetings(candidate, date(2026, 8, 24))

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["date"], date(2026, 8, 31))
        self.assertEqual(meetings[0]["time"], "14:00")
        self.assertEqual(meetings[0]["confidence"], 0.55)

    def test_explicit_meeting_subject_can_keep_an_all_day_semantic_event(self):
        candidate = record(
            "Webinar daveti",
            "31 Ağustos 2026 tarihinde gerçekleştirilecektir.",
        )

        meetings = extract_meetings(candidate, date(2026, 8, 24))

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["time"], "Tüm gün")

    def test_verified_low_confidence_meeting_remains_in_attention_output(self):
        candidate = record(
            "Ağustos değerlendirmesi",
            "31 Ağustos 2026 saat 14:00'te toplantı yapacağız.",
        )

        digest = format_attention_digest([candidate], date(2026, 8, 24))

        self.assertIn("Ağustos değerlendirmesi", digest)
        self.assertIn("düşük güvenli ayrıştırma", digest)


if __name__ == "__main__":
    unittest.main()
