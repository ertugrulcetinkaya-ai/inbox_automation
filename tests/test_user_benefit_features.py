import tempfile
import unittest
import stat
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from mail_digest.cli import _run_digest
from mail_digest.services.meeting_service import (
    due_reminder_meetings,
    format_attention_digest,
    format_digest,
    format_reminder_digest,
    format_upcoming_digest,
    format_weekly_digest,
)
from mail_digest.services.reminder_state import (
    load_last_successful_reminder_scan,
    load_sent_reminders,
    mark_reminder_scan_succeeded,
    mark_reminders_sent,
    reminder_key,
)


def ics_record(
    uid,
    start,
    end=None,
    summary="Fixture toplantısı",
    status="CONFIRMED",
):
    end_line = f"DTEND;TZID=Europe/Istanbul:{end}\n" if end else ""
    return {
        "source_message_id": f"<{uid}@example.com>",
        "sender": "Fixture Organizer <organizer@example.com>",
        "subject": summary,
        "content": (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "BEGIN:VEVENT\n"
            f"UID:{uid}\n"
            f"DTSTART;TZID=Europe/Istanbul:{start}\n"
            f"{end_line}"
            f"SUMMARY:{summary}\n"
            f"STATUS:{status}\n"
            "LOCATION:Toplantı Odası\n"
            "DESCRIPTION:https://teams.microsoft.com/l/meetup-join/fixture\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        ),
    }


class MeetingReminderTests(unittest.TestCase):
    def test_selects_only_timed_meetings_in_the_reminder_window(self):
        records = [
            ics_record("due", "20260824T100000", summary="Yaklaşan toplantı"),
            ics_record("later", "20260824T101000", summary="Daha sonraki toplantı"),
            ics_record("all-day", "20260824", summary="Tüm gün etkinliği"),
        ]

        due = due_reminder_meetings(
            records,
            now=datetime(2026, 8, 24, 9, 45),
            lead_minutes=15,
            window_minutes=5,
        )

        self.assertEqual([meeting["subject"] for meeting in due], ["Yaklaşan toplantı"])
        reminder = format_reminder_digest(due, now=datetime(2026, 8, 24, 9, 45))
        self.assertIn("15 dk kaldı", reminder)
        self.assertIn("Toplantı Odası", reminder)
        self.assertIn("teams.microsoft.com", reminder)

        drifted = due_reminder_meetings(
            records,
            now=datetime(2026, 8, 24, 9, 45, 20),
            lead_minutes=15,
            window_minutes=5,
        )
        self.assertEqual([meeting["subject"] for meeting in drifted], ["Yaklaşan toplantı"])

    def test_catch_up_since_last_successful_scan_keeps_a_missed_tick(self):
        records = [ics_record("catch-up", "20260824T091200")]

        due = due_reminder_meetings(
            records,
            now=datetime(2026, 8, 24, 9, 5),
            lead_minutes=15,
            window_minutes=5,
            since=datetime(2026, 8, 24, 9, 0),
        )

        self.assertEqual([meeting["subject"] for meeting in due], ["Fixture toplantısı"])

    def test_first_scan_has_recovery_overlap_when_no_cursor_exists(self):
        records = [ics_record("first-recovery", "20260824T091200")]

        due = due_reminder_meetings(
            records,
            now=datetime(2026, 8, 24, 9, 5),
            lead_minutes=15,
            window_minutes=5,
        )

        self.assertEqual(len(due), 1)

    def test_successful_reminder_keys_are_persisted_without_private_titles(self):
        meeting = due_reminder_meetings(
            [ics_record("private-uid", "20260824T100000", summary="Özel toplantı başlığı")],
            now=datetime(2026, 8, 24, 9, 45),
        )[0]

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "reminders.json"
            mark_reminders_sent({}, [meeting], now=datetime(2026, 8, 24, 9, 45), state_file=state_file)
            sent = load_sent_reminders(state_file=state_file, now=datetime(2026, 8, 24, 9, 46))

            self.assertIn(reminder_key(meeting), sent)
            self.assertNotIn("Özel toplantı başlığı", state_file.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)

    def test_successful_scan_cursor_is_persisted_with_legacy_state_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "reminders.json"
            mark_reminder_scan_succeeded(
                scan_at=datetime(2026, 8, 24, 9, 45),
                state_file=state_file,
            )

            self.assertEqual(
                load_last_successful_reminder_scan(
                    state_file=state_file,
                    now=datetime(2026, 8, 24, 9, 46),
                ),
                datetime(2026, 8, 24, 9, 45),
            )

    def test_utc_ics_is_reminded_in_istanbul_local_time_across_midnight(self):
        record = {
            "source_message_id": "<utc-reminder@example.com>",
            "sender": "Fixture Organizer <organizer@example.com>",
            "subject": "UTC toplantısı",
            "content": (
                "BEGIN:VCALENDAR\n"
                "VERSION:2.0\n"
                "BEGIN:VEVENT\n"
                "UID:utc-reminder\n"
                "DTSTART:20260823T220000Z\n"
                "SUMMARY:UTC toplantısı\n"
                "STATUS:CONFIRMED\n"
                "END:VEVENT\n"
                "END:VCALENDAR\n"
            ),
        }

        due = due_reminder_meetings(
            [record],
            now=datetime(2026, 8, 24, 0, 45),
            lead_minutes=15,
            window_minutes=5,
        )

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["date"], date(2026, 8, 24))
        self.assertEqual(due[0]["time"], "01:00")

    def test_rescheduled_ics_old_occurrence_never_triggers_a_reminder(self):
        old = ics_record("moved", "20260824T100000", summary="Taşınan toplantı")
        updated = ics_record("moved", "20260825T100000", summary="Taşınan toplantı")
        updated["content"] = updated["content"].replace(
            "STATUS:CONFIRMED",
            "SEQUENCE:1\nSTATUS:CONFIRMED",
        )

        due = due_reminder_meetings(
            [old, updated],
            now=datetime(2026, 8, 24, 9, 45),
            lead_minutes=15,
            window_minutes=5,
        )

        self.assertEqual(due, [])

    def test_cli_sends_once_and_marks_state_only_after_success(self):
        meeting = due_reminder_meetings(
            [ics_record("due-cli", "20260824T100000")],
            now=datetime(2026, 8, 24, 9, 45),
        )[0]
        mark_sent = Mock()

        with (
            patch("mail_digest.cli.fetch_mail", return_value=[{"fixture": True}]),
            patch("mail_digest.cli.selected_source_name", return_value="gmail"),
            patch("mail_digest.cli.due_reminder_meetings", return_value=[meeting]),
            patch("mail_digest.cli.load_sent_reminders", return_value={}),
            patch("mail_digest.cli.send_telegram", return_value=True) as send,
            patch("mail_digest.cli.mark_reminders_sent", mark_sent),
        ):
            self.assertEqual(_run_digest(mode="reminder"), 0)

        send.assert_called_once()
        mark_sent.assert_called_once()

        mark_sent.reset_mock()
        with (
            patch("mail_digest.cli.fetch_mail", return_value=[{"fixture": True}]),
            patch("mail_digest.cli.selected_source_name", return_value="gmail"),
            patch("mail_digest.cli.due_reminder_meetings", return_value=[meeting]),
            patch("mail_digest.cli.load_sent_reminders", return_value={}),
            patch("mail_digest.cli.send_telegram", return_value=False),
            patch("mail_digest.cli.mark_reminders_sent", mark_sent),
        ):
            self.assertEqual(_run_digest(mode="reminder"), 1)

        mark_sent.assert_not_called()

    def test_cli_does_not_send_when_no_unsent_reminder_is_due(self):
        with (
            patch("mail_digest.cli.fetch_mail", return_value=[]),
            patch("mail_digest.cli.selected_source_name", return_value="gmail"),
            patch("mail_digest.cli.due_reminder_meetings", return_value=[]),
            patch("mail_digest.cli.load_sent_reminders", return_value={}),
            patch("mail_digest.cli.mark_reminder_scan_succeeded"),
            patch("mail_digest.cli.send_telegram") as send,
        ):
            self.assertEqual(_run_digest(mode="reminder"), 0)

        send.assert_not_called()

    def test_cli_does_not_repeat_an_already_sent_occurrence(self):
        meeting = due_reminder_meetings(
            [ics_record("already-sent", "20260824T100000")],
            now=datetime(2026, 8, 24, 9, 45),
        )[0]
        sent = {reminder_key(meeting): "2026-08-24T09:45:00"}

        with (
            patch("mail_digest.cli.fetch_mail", return_value=[{"fixture": True}]),
            patch("mail_digest.cli.selected_source_name", return_value="gmail"),
            patch("mail_digest.cli.due_reminder_meetings", return_value=[meeting]),
            patch("mail_digest.cli.load_sent_reminders", return_value=sent),
            patch("mail_digest.cli.mark_reminder_scan_succeeded"),
            patch("mail_digest.cli.send_telegram") as send,
        ):
            self.assertEqual(_run_digest(mode="reminder"), 0)

        send.assert_not_called()


class WeeklyDigestTests(unittest.TestCase):
    def test_groups_seven_days_and_marks_conflicts_and_short_breaks(self):
        records = [
            ics_record("first", "20260824T100000", "20260824T110000", "İlk toplantı"),
            ics_record("overlap", "20260824T103000", "20260824T113000", "Çakışan toplantı"),
            ics_record("close", "20260824T113500", "20260824T120000", "Yakın toplantı"),
            ics_record("next-day", "20260825T090000", "20260825T093000", "Ertesi gün"),
            ics_record("outside", "20260831T090000", "20260831T093000", "Dönem dışı"),
        ]

        digest = format_weekly_digest(records, date(2026, 8, 24))

        self.assertIn("Pazartesi — 24 Ağustos 2026", digest)
        self.assertIn("Salı — 25 Ağustos 2026", digest)
        self.assertGreaterEqual(digest.count("Çakışma"), 2)
        self.assertIn("yalnızca 5 dk ara", digest)
        self.assertNotIn("Dönem dışı", digest)


class AttentionDigestTests(unittest.TestCase):
    def test_separates_tentative_rescheduled_and_low_confidence_meetings(self):
        records = [
            ics_record("confirmed", "20260824T100000", summary="Kesin ICS"),
            ics_record(
                "tentative",
                "20260824T110000",
                summary="Kesinleşmemiş ICS",
                status="TENTATIVE",
            ),
            {
                "sender": "Fixture Organizer <organizer@example.com>",
                "subject": "Ertelenen satış toplantısı",
                "content": "24 Ağustos toplantısı 25 Ağustos 14:00'e ertelendi.",
            },
            {
                "sender": "Fixture Organizer <organizer@example.com>",
                "subject": "Metin toplantısı",
                "content": "26 Ağustos 2026 saat 10:00 toplantı yapılacaktır.",
            },
        ]

        attention = format_attention_digest(records, date(2026, 8, 24))
        daily = format_digest(records, date(2026, 8, 24))
        upcoming = format_upcoming_digest(records, date(2026, 8, 24))
        weekly = format_weekly_digest(records, date(2026, 8, 24))

        self.assertNotIn("Kesin ICS", attention)
        self.assertIn("Kesinleşmemiş ICS", attention)
        self.assertIn("Ertelenen satış toplantısı", attention)
        self.assertIn("Metin toplantısı", attention)
        self.assertIn("düşük güvenli ayrıştırma", attention)
        self.assertIn("⚠️ Dikkat gerektirenler", daily)
        self.assertIn("⚠️ Dikkat gerektirenler", upcoming)
        self.assertIn("⚠️ Dikkat gerektirenler", weekly)
        self.assertEqual(upcoming.count("Kesinleşmemiş ICS"), 1)
        self.assertEqual(weekly.count("Kesinleşmemiş ICS"), 1)

    def test_cancelled_events_are_never_listed_as_attention(self):
        cancelled = ics_record(
            "cancelled",
            "20260824T100000",
            summary="İptal edilmiş toplantı",
            status="CANCELLED",
        )

        attention = format_attention_digest([cancelled], date(2026, 8, 24))

        self.assertNotIn("İptal edilmiş toplantı", attention)
        self.assertIn("dikkat gerektiren toplantı yok", attention)


if __name__ == "__main__":
    unittest.main()
