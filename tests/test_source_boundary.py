"""Transport -> parser boundary regression tests.

These validate the chain:

    simulated osascript stdout
        -> real fetch_mail()
        -> real record parsing
        -> real extract_meetings()

They prove that the transport no longer assumes pre-filtered candidates: every
emitted record (meeting or not) survives ``fetch_mail()`` and is handed to the
parser, which alone decides whether it contains a meeting.

They do NOT prove that AppleScript detects attachments or calendar signals
correctly; that is the source layer's job and is covered separately.

Transport detail: each simulated record stays on ONE physical stdout line.
Internal newlines in ``content`` / ``raw_source`` are encoded with the repo's
transport newline token (``__MAIL_DIGEST_LINEBREAK__``), because ``fetch_mail()``
splits stdout into physical lines first.
"""

import subprocess
import unittest
from datetime import date
from unittest.mock import patch

from mail_digest.config import FIELD_DELIMITER, TRANSPORT_NEWLINE_TOKEN
from mail_digest.parsing.meeting_parser import extract_meetings
from mail_digest.sources.apple_mail import fetch_mail


# Apple Mail's `date received as string` shape, parsed by parse_received_date.
DATE_AUG14 = "Friday, August 14, 2026 at 09:00:00"


class SourceBoundaryTests(unittest.TestCase):
    def _completed(self, stdout=""):
        return subprocess.CompletedProcess(
            args=["osascript"], returncode=0, stdout=stdout, stderr=""
        )

    def _encode_record(self, subject, content, date_text, raw_source="",
                       sender="Fixture Sender <sender@example.com>",
                       source_message_id="<fixture@example.com>"):
        """Build one physical-line transport record, encoding internal newlines."""

        def encode(value):
            return (value or "").replace("\n", TRANSPORT_NEWLINE_TOKEN)

        fields = [
            "Account", "Inbox", sender, subject, date_text,
            content, source_message_id, raw_source,
        ]
        return FIELD_DELIMITER.join(encode(field) for field in fields)

    def _ics_mime(self, uid, start, end, summary):
        """Parser-compatible multipart MIME with a text/calendar attachment."""
        return (
            "Content-Type: multipart/mixed; boundary=cal-boundary\n"
            "\n"
            "--cal-boundary\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            "Toplanti davetiyesi\n"
            "--cal-boundary\n"
            "Content-Type: text/calendar; charset=utf-8; method=REQUEST\n"
            "Content-Disposition: attachment; filename=invite.ics\n"
            "\n"
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "METHOD:REQUEST\n"
            "BEGIN:VEVENT\n"
            f"UID:{uid}\n"
            f"DTSTART;TZID=Europe/Istanbul:{start}\n"
            f"DTEND;TZID=Europe/Istanbul:{end}\n"
            f"SUMMARY:{summary}\n"
            "ORGANIZER;CN=Organizer:mailto:organizer@example.com\n"
            "STATUS:CONFIRMED\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
            "--cal-boundary--\n"
        )

    def _fetch_records(self, stdout):
        with patch(
            "mail_digest.sources.apple_mail._run_applescript",
            return_value=self._completed(stdout),
        ):
            return fetch_mail()

    def test_a_turkish_invitation_with_authoritative_ics(self):
        raw = self._ics_mime(
            "davet-q3@example.com", "20260815T140000", "20260815T143000", "Q3 Review"
        )
        stdout = self._encode_record(
            "Davet: Q3 Review", "Davetiye ekte.", DATE_AUG14, raw_source=raw
        )

        records = self._fetch_records(stdout)

        # The record crosses the transport boundary intact.
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["subject"], "Davet: Q3 Review")

        # The authoritative ICS is extracted with UID and date/time preserved.
        meetings = extract_meetings(records[0], date(2026, 8, 14))
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["uid"], "davet-q3@example.com")
        self.assertEqual(meetings[0]["date"], date(2026, 8, 15))
        self.assertEqual(meetings[0]["time"], "14:00–14:30")

    def test_b_neutral_subject_body_only_evidence_survives(self):
        # Primary Gate-1 regression: the subject has no meeting keyword, so the old
        # subject gate would have dropped this before Python ever saw the body.
        stdout = self._encode_record(
            "Q3 Review",
            "Toplantımız bugün saat 14.30'da yapılacaktır.",
            DATE_AUG14,
        )

        records = self._fetch_records(stdout)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["subject"], "Q3 Review")

        meetings = extract_meetings(records[0], date(2026, 8, 14))
        self.assertEqual(len(meetings), 1)
        # "bugün" anchors to the received date; dotted time is parsed as a time.
        self.assertEqual(meetings[0]["date"], date(2026, 8, 14))
        self.assertEqual(meetings[0]["time"], "14:30")

    def test_c_ordinary_non_meeting_record_survives_with_empty_raw_source(self):
        stdout = self._encode_record(
            "Güncel bilgi",
            "Merhaba, dosyayı ekte gönderiyorum. İyi çalışmalar.",
            DATE_AUG14,
        )

        records = self._fetch_records(stdout)

        # The record still crosses the boundary even though it is not a meeting.
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["raw_source"], "")

        # And the parser correctly finds no meeting in it.
        self.assertEqual(extract_meetings(records[0], date(2026, 8, 14)), [])

    def test_d_english_invitation_mime_ics_unchanged(self):
        raw = self._ics_mime(
            "en-invite@example.com", "20260817T100000", "20260817T103000", "Q3 Review"
        )
        stdout = self._encode_record(
            "Invitation: Q3 Review", "You are invited.", DATE_AUG14, raw_source=raw
        )

        records = self._fetch_records(stdout)

        self.assertEqual(len(records), 1)
        meetings = extract_meetings(records[0], date(2026, 8, 14))
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["uid"], "en-invite@example.com")
        self.assertEqual(meetings[0]["date"], date(2026, 8, 17))
        self.assertEqual(meetings[0]["time"], "10:00–10:30")

    def test_e_turkish_davet_prefix_without_ics_reaches_date_extraction(self):
        # No generic meeting keyword in subject or body; only the subject-specific
        # "Davet:" prefix lets the semantic parser reach date/time extraction.
        stdout = self._encode_record(
            "Davet: Q3 Review",
            "25 Ağustos 2026 saat 14:00'te katılımınızı bekliyoruz.",
            DATE_AUG14,
        )

        records = self._fetch_records(stdout)

        self.assertEqual(len(records), 1)
        meetings = extract_meetings(records[0], date(2026, 8, 14))
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["date"], date(2026, 8, 25))
        self.assertEqual(meetings[0]["time"], "14:00")

    def test_e_negative_bare_davet_in_body_is_not_a_meeting(self):
        # Ordinary prose containing the bare word "davet" must not become a meeting.
        stdout = self._encode_record(
            "Mektup",
            "Bu bir davet mektubu değildir, lütfen okuyunuz.",
            DATE_AUG14,
        )

        records = self._fetch_records(stdout)

        self.assertEqual(len(records), 1)
        self.assertEqual(extract_meetings(records[0], date(2026, 8, 14)), [])

    def test_f_mixed_multi_record_boundary(self):
        semantic = self._encode_record(
            "Q3 Review",
            "Toplantımız bugün saat 14.30'da yapılacaktır.",
            DATE_AUG14,
        )
        ics_raw = self._ics_mime(
            "mixed-ics@example.com", "20260816T090000", "20260816T093000", "Sync"
        )
        ics = self._encode_record(
            "Invitation: Sync", "You are invited.", DATE_AUG14, raw_source=ics_raw
        )
        ordinary = self._encode_record(
            "Fatura", "Merhaba, dosyayı ekte gönderiyorum.", DATE_AUG14
        )

        # All three records are emitted together, one per physical line.
        stdout = "\n".join([semantic, ics, ordinary])

        records = self._fetch_records(stdout)

        # Transport no longer assumes pre-filtered candidates: all survive.
        self.assertEqual(len(records), 3)

        # Only the actual meeting records produce meetings.
        self.assertTrue(extract_meetings(records[0], date(2026, 8, 14)))
        self.assertTrue(extract_meetings(records[1], date(2026, 8, 14)))
        self.assertEqual(extract_meetings(records[2], date(2026, 8, 14)), [])


if __name__ == "__main__":
    unittest.main()
