import unittest
from datetime import date

from main import extract_meeting, extract_meetings, parse_ics_meetings
from fixture_helpers import load_fixture


FIXTURE_CASES = [
    ("dotted_time_10_30", "dotted_time_10_30.txt", date(2026, 8, 12), date(2026, 8, 12), "10:30"),
    ("dotted_time_09_30", "dotted_time_09_30.txt", date(2026, 8, 12), date(2026, 8, 12), "09:30"),
    ("dotted_time_14_30", "dotted_time_14_30.txt", date(2026, 8, 12), date(2026, 8, 12), "14:30"),
    ("dotted_time_range", "dotted_time_range.txt", date(2026, 8, 12), date(2026, 8, 12), "09:30–10:30"),
    ("dotted_date", "date_dotted_15_08.txt", date(2026, 8, 15), date(2026, 8, 15), "09:00"),
    ("us_date", "date_us_08_15_2026.txt", date(2026, 8, 15), date(2026, 8, 15), "10:00"),
    ("year_rollover", "date_december_january.txt", date(2027, 1, 5), date(2027, 1, 5), "10:00"),
    ("bare_cuma", "weekday_cuma.txt", date(2026, 8, 14), date(2026, 8, 14), "14:00"),
    ("bu_cuma", "weekday_bu_cuma.txt", date(2026, 8, 14), date(2026, 8, 14), "14:00"),
    ("onumuzdeki_cuma", "weekday_onumuzdeki_cuma.txt", date(2026, 8, 14), date(2026, 8, 14), "14:00"),
    ("this_friday", "weekday_this_friday.txt", date(2026, 8, 14), date(2026, 8, 14), "14:00"),
    ("next_friday", "weekday_next_friday.txt", date(2026, 8, 14), date(2026, 8, 14), "14:00"),
    ("cancelled_turkish", "cancelled_tr.txt", date(2026, 8, 15), None, None),
    ("cancelled_english", "cancelled_en.txt", date(2026, 8, 15), None, None),
    ("reschedule_turkish", "reschedule_tr.txt", date(2026, 8, 18), date(2026, 8, 18), "14:00"),
    ("reschedule_english", "reschedule_en.txt", date(2026, 8, 18), date(2026, 8, 18), "14:00"),
    ("multiple_dates", "multiple_dates.txt", date(2026, 8, 14), "multiple", None),
    ("quoted_reply", "quoted_reply.txt", date(2026, 8, 18), date(2026, 8, 18), "14:00"),
    ("neutral_subject", "neutral_subject_teams.txt", date(2026, 8, 15), date(2026, 8, 15), "10:00"),
    ("ics_fixture", "ics_duplicate.ics", date(2026, 8, 15), "ics", None),
]


class FixtureParserTests(unittest.TestCase):
    pass


def _fixture_test(case):
    label, fixture_name, target_date, expected_date, expected_time = case

    def test(self):
        record = load_fixture(fixture_name)
        if expected_date is None:
            self.assertIsNone(extract_meeting(record, target_date))
            return
        if expected_date == "multiple":
            meetings = extract_meetings(record, target_date)
            self.assertEqual(
                [meeting["date"] for meeting in meetings],
                [date(2026, 8, 15), date(2026, 8, 18)],
            )
            return
        if expected_date == "ics":
            meetings = parse_ics_meetings(record)
            self.assertEqual(len(meetings), 1)
            self.assertEqual(meetings[0].uid, "fixture-duplicate@example.com")
            return

        meeting = extract_meeting(record, target_date)
        self.assertIsNotNone(meeting, label)
        self.assertEqual(meeting["date"], expected_date)
        self.assertEqual(meeting["time"], expected_time)
        if "reschedule" in label:
            self.assertEqual(meeting["status"], "RESCHEDULED")

    return test


for _case in FIXTURE_CASES:
    setattr(FixtureParserTests, f"test_fixture_{_case[0]}", _fixture_test(_case))


if __name__ == "__main__":
    unittest.main()
