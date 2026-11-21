import unittest
from datetime import date
from unittest.mock import patch

from main import extract_meeting, extract_meetings
from mail_digest.services.meeting_service import build_digest_result
from parse_cache_test_support import isolate_parse_cache

isolate_parse_cache()


def _record(content, received_date=date(2026, 8, 12), subject="Toplantı"):
    return {
        "received_date": received_date,
        "sender": "Fixture Organizer <organizer@example.com>",
        "subject": subject,
        "content": content,
    }


TIME_CASES = [
    ("dot_10_30", "Toplantı bugün saat 10.30'da yapılacak.", date(2026, 8, 12), "10:30"),
    ("dot_09_30", "Toplantı bugün saat 09.30'da yapılacak.", date(2026, 8, 12), "09:30"),
    ("dot_14_30", "Toplantı bugün saat 14.30'da yapılacak.", date(2026, 8, 12), "14:30"),
    ("dot_range", "Toplantı bugün 09.30 – 10.30 arasında yapılacak.", date(2026, 8, 12), "09:30–10:30"),
    ("colon_10_30", "Toplantı bugün saat 10:30'da yapılacak.", date(2026, 8, 12), "10:30"),
    ("english_pm", "Meeting today at 2:30 PM.", date(2026, 8, 12), "14:30"),
    ("english_am", "Meeting today at 7:05 AM.", date(2026, 8, 12), "07:05"),
    ("dot_no_leading_zero", "Toplantı bugün saat 9.30'da yapılacak.", date(2026, 8, 12), "09:30"),
    ("dot_noon", "Toplantı bugün saat 12.00'de yapılacak.", date(2026, 8, 12), "12:00"),
    ("dot_with_time_word", "Toplantımız bugün at 14.30 yapılacak.", date(2026, 8, 12), "14:30"),
]

DATE_CASES = [
    ("dotted_day_month", "Toplantı 15.08 saat 09:00'da.", date(2026, 8, 15), date(2026, 8, 15)),
    ("dotted_with_year", "Toplantı 15.08.2026 saat 09:00'da.", date(2026, 8, 15), date(2026, 8, 15)),
    ("us_numeric", "Meeting on 08/15/2026 at 10:00.", date(2026, 8, 15), date(2026, 8, 15)),
    ("iso_numeric", "Meeting on 2026-08-15 at 11:00.", date(2026, 8, 15), date(2026, 8, 15)),
    ("turkish_day_month", "Toplantı 15 Ağustos saat 09:00'da.", date(2026, 8, 15), date(2026, 8, 15)),
    ("turkish_month_day", "Toplantı Ağustos 15 saat 09:00'da.", date(2026, 8, 15), date(2026, 8, 15)),
    ("turkish_kasim_upper", "Toplantı 22 KASIM saat 14:00'da.", date(2026, 11, 22), date(2026, 11, 22)),
    ("turkish_kasim_mixed", "Toplantı 22 Kasım saat 14:00'da.", date(2026, 11, 22), date(2026, 11, 22)),
    ("ascii_kasim", "Toplantı 22 kasim saat 14:00'da.", date(2026, 11, 22), date(2026, 11, 22)),
    ("ascii_subat", "Toplantı 22 subat saat 14:00'da.", date(2027, 2, 22), date(2027, 2, 22)),
    ("ascii_agustos", "Toplantı 22 agustos saat 14:00'da.", date(2026, 8, 22), date(2026, 8, 22)),
    ("ascii_eylul", "Toplantı 22 eylul saat 14:00'da.", date(2026, 9, 22), date(2026, 9, 22)),
    ("ascii_aralik", "Toplantı 22 aralik saat 14:00'da.", date(2026, 12, 22), date(2026, 12, 22)),
    ("ascii_mayis", "Toplantı 22 mayis saat 14:00'da.", date(2027, 5, 22), date(2027, 5, 22)),
    ("english_day_month", "Meeting on 15 August at 09:00.", date(2026, 8, 15), date(2026, 8, 15)),
    ("english_month_day", "Meeting on August 15 at 09:00.", date(2026, 8, 15), date(2026, 8, 15)),
    ("slash_day_month", "Toplantı 15/08 saat 09:00'da.", date(2026, 8, 15), date(2026, 8, 15)),
    ("dash_day_month", "Toplantı 15-08 saat 09:00'da.", date(2026, 8, 15), date(2026, 8, 15)),
    ("dotted_date_context", "Toplantı tarihi 15.08, saat 09:00.", date(2026, 8, 15), date(2026, 8, 15)),
    ("december_january", "Toplantı 5 Ocak saat 10:00'da.", date(2027, 1, 5), date(2027, 1, 5)),
]

WEEKDAY_CASES = [
    ("pazartesi", "Pazartesi saat 09:00.", date(2026, 8, 10), date(2026, 8, 10)),
    ("sali", "Salı saat 09:00.", date(2026, 8, 11), date(2026, 8, 11)),
    ("carsamba", "Çarşamba saat 09:00.", date(2026, 8, 12), date(2026, 8, 12)),
    ("persembe", "Perşembe saat 09:00.", date(2026, 8, 13), date(2026, 8, 13)),
    ("cuma", "Cuma saat 09:00.", date(2026, 8, 14), date(2026, 8, 14)),
    ("cumartesi", "Cumartesi saat 09:00.", date(2026, 8, 15), date(2026, 8, 15)),
    ("pazar", "Pazar saat 09:00.", date(2026, 8, 16), date(2026, 8, 16)),
    ("ascii_sali", "Sali saat 09:00.", date(2026, 8, 11), date(2026, 8, 11)),
    ("ascii_carsamba", "Carsamba saat 09:00.", date(2026, 8, 12), date(2026, 8, 12)),
    ("ascii_persembe", "Persembe saat 09:00.", date(2026, 8, 13), date(2026, 8, 13)),
    ("bu_cuma", "Bu cuma saat 14:00.", date(2026, 8, 14), date(2026, 8, 14)),
    ("onumuzdeki_cuma", "Önümüzdeki cuma saat 14:00.", date(2026, 8, 14), date(2026, 8, 14)),
    ("this_friday", "This Friday at 2:00 PM.", date(2026, 8, 14), date(2026, 8, 14)),
    ("next_friday", "Next Friday at 2:00 PM.", date(2026, 8, 14), date(2026, 8, 14)),
]

SEMANTIC_CASES = [
    ("cancel_turkish", _record("15 Ağustos 2026 saat 10:00 toplantısı iptal edilmiştir."), date(2026, 8, 15), "none"),
    ("cancel_turkish_upper", _record("22 KASIM saat 14:00 toplantı İPTAL EDİLDİ", received_date=date(2026, 11, 21)), date(2026, 11, 22), "none"),
    ("cancel_english", _record("The meeting on August 15, 2026 at 10:00 AM was cancelled."), date(2026, 8, 15), "none"),
    ("canceled_english", _record("The meeting on August 15, 2026 at 10:00 AM was canceled."), date(2026, 8, 15), "none"),
    ("reschedule_turkish", _record("15 Ağustos toplantımız 18 Ağustos 14:00'e ertelendi."), date(2026, 8, 18), {"date": date(2026, 8, 18), "time": "14:00", "status": "RESCHEDULED"}),
    ("reschedule_english", _record("The August 15 meeting was rescheduled to August 18 at 2:00 PM."), date(2026, 8, 18), {"date": date(2026, 8, 18), "time": "14:00", "status": "RESCHEDULED"}),
    ("tentative_turkish", _record("15 Ağustos saat 10:00 için taslak toplantı."), date(2026, 8, 15), {"date": date(2026, 8, 15), "time": "10:00", "status": "TENTATIVE"}),
    ("tentative_english", _record("Tentative meeting on August 15 at 10:00 AM."), date(2026, 8, 15), {"date": date(2026, 8, 15), "time": "10:00", "status": "TENTATIVE"}),
    ("quoted_old_date", _record("Güncel toplantımız 18 Ağustos 2026 saat 14:00'te yapılacak.\n> Eski toplantı 15 Ağustos 2026 saat 10:00'daydı."), date(2026, 8, 18), {"date": date(2026, 8, 18), "time": "14:00"}),
    ("quoted_header", _record("Güncel toplantımız 18 Ağustos 2026 saat 14:00'te yapılacak.\nOn Tuesday, August 11, 2026, Fixture Organizer wrote:\n15 Ağustos 2026 saat 10:00 toplantısı."), date(2026, 8, 18), {"date": date(2026, 8, 18), "time": "14:00"}),
    ("neutral_subject_body", _record("Microsoft Teams toplantısına katılın. 15 Ağustos 2026 saat 10:00'da görüşeceğiz." , subject="Ağustos değerlendirmesi"), date(2026, 8, 15), {"date": date(2026, 8, 15), "time": "10:00"}),
    ("multiple_dates_first", _record("Toplantı 15 Ağustos saat 10:00 ve 18 Ağustos saat 14:00 tarihlerinde yapılacak."), date(2026, 8, 14), "multiple"),
    ("multiple_dates_second", _record("Toplantı 15 Ağustos saat 10:00 ve 18 Ağustos saat 14:00 tarihlerinde yapılacak."), date(2026, 8, 18), {"date": date(2026, 8, 18), "time": "14:00"}),
    ("relative_today_is_received_date", _record("Toplantı bugün saat 10:00.", received_date=date(2026, 8, 12)), date(2026, 8, 12), {"date": date(2026, 8, 12), "time": "10:00"}),
    ("relative_tomorrow_is_received_date", _record("Toplantı yarın saat 10:00.", received_date=date(2026, 8, 12)), date(2026, 8, 13), {"date": date(2026, 8, 13), "time": "10:00"}),
]


class ParserMatrixTests(unittest.TestCase):
    pass


class TurkishCaseRegressionTests(unittest.TestCase):
    def test_uppercase_tomorrow_uses_received_date_for_daily_digest(self):
        record = _record(
            "YARIN saat 10:00'da toplantı var.",
            received_date=date(2026, 11, 21),
        )

        result = build_digest_result(
            [record], "daily", target_date=date(2026, 11, 22)
        )

        self.assertEqual(result.count, 1)
        self.assertIn("10:00", result.message)

    def test_uppercase_month_and_valid_record_both_render_in_daily_digest(self):
        upper_month = _record(
            "22 KASIM saat 14:00 toplantısı.",
            received_date=date(2026, 11, 21),
        )
        valid = _record(
            "22 Kasım saat 15:00 toplantısı.",
            received_date=date(2026, 11, 21),
        )

        result = build_digest_result(
            [upper_month, valid], "daily", target_date=date(2026, 11, 22)
        )

        self.assertEqual(result.count, 2)
        self.assertIn("14:00", result.message)
        self.assertIn("15:00", result.message)

    def test_one_record_parse_error_does_not_hide_a_valid_record(self):
        records = [
            {"subject": "bad synthetic record", "content": "bad"},
            {"subject": "valid synthetic record", "content": "valid"},
        ]
        valid_meeting = {
            "subject": "Valid synthetic meeting",
            "sender": "Fixture Organizer <organizer@example.com>",
            "date": date(2026, 11, 22),
            "time": "10:00",
            "sort_minutes": 600,
        }

        with patch(
            "mail_digest.services.meeting_service.extract_meetings",
            side_effect=[ValueError("synthetic parser failure"), [valid_meeting]],
        ):
            result = build_digest_result(
                records, "daily", target_date=date(2026, 11, 22)
            )

        self.assertEqual(result.count, 1)
        self.assertIn("Valid synthetic meeting", result.message)


def _time_test(case):
    label, content, target_date, expected_time = case

    def test(self):
        meeting = extract_meeting(_record(content), target_date)
        self.assertIsNotNone(meeting, label)
        self.assertEqual(meeting["date"], target_date)
        self.assertEqual(meeting["time"], expected_time)

    return test


def _date_test(case):
    label, content, target_date, expected_date = case

    def test(self):
        received = date(2026, 12, 20) if label == "december_january" else date(2026, 8, 12)
        meeting = extract_meeting(_record(content, received_date=received), target_date)
        self.assertIsNotNone(meeting, label)
        self.assertEqual(meeting["date"], expected_date)

    return test


def _weekday_test(case):
    label, content, target_date, expected_date = case

    def test(self):
        received = date(2026, 8, 12) if label not in {"pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar", "ascii_sali", "ascii_carsamba", "ascii_persembe"} else date(2026, 8, 10)
        meeting = extract_meeting(_record(content, received_date=received), target_date)
        self.assertIsNotNone(meeting, label)
        self.assertEqual(meeting["date"], expected_date)

    return test


def _semantic_test(case):
    label, record, target_date, expected = case

    def test(self):
        if expected == "none":
            self.assertIsNone(extract_meeting(record, target_date))
            return
        if expected == "multiple":
            meetings = extract_meetings(record, target_date)
            self.assertEqual(len(meetings), 2)
            return
        meeting = extract_meeting(record, target_date)
        self.assertIsNotNone(meeting, label)
        for key, value in expected.items():
            self.assertEqual(meeting[key], value)

    return test


for _case in TIME_CASES:
    setattr(ParserMatrixTests, f"test_time_{_case[0]}", _time_test(_case))
for _case in DATE_CASES:
    setattr(ParserMatrixTests, f"test_date_{_case[0]}", _date_test(_case))
for _case in WEEKDAY_CASES:
    setattr(ParserMatrixTests, f"test_weekday_{_case[0]}", _weekday_test(_case))
for _case in SEMANTIC_CASES:
    setattr(ParserMatrixTests, f"test_semantic_{_case[0]}", _semantic_test(_case))


if __name__ == "__main__":
    unittest.main()
