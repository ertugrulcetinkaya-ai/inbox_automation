import base64
import unittest
from datetime import date

from mail_digest.parsing.meeting_parser import extract_meetings
from mail_digest.sources.gmail.api import GmailApiError
from mail_digest.sources.gmail.mime import MessageStructureError, normalize_message


def b64(value):
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def message(payload, message_id="m1", labels=None, internal_date="1787068800000"):
    return {
        "id": message_id,
        "threadId": "t1",
        "internalDate": internal_date,
        "labelIds": ["INBOX"] if labels is None else labels,
        "payload": payload,
    }


def payload(parts=None, mime_type="multipart/mixed", data=None, headers=None, filename=""):
    value = {
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers or [],
        "body": {},
    }
    if data is not None:
        value["body"]["data"] = data
    if parts is not None:
        value["parts"] = parts
    return value


class GmailMimeTests(unittest.TestCase):
    def normalize(self, value, attachments=None):
        self.attachment_calls = []
        self.raw_calls = []
        attachments = attachments or {}

        def attachment_loader(message_id, attachment_id):
            self.attachment_calls.append((message_id, attachment_id))
            return attachments[attachment_id]

        def raw_loader(message_id):
            self.raw_calls.append(message_id)
            return {"raw": b64("Content-Type: text/calendar\n\nBEGIN:VCALENDAR\nEND:VCALENDAR")}

        return normalize_message(value, attachment_loader, raw_loader)

    def test_utf8_turkish_plain_and_encoded_headers(self):
        root = payload(
            parts=[payload(mime_type="text/plain", data=b64("Toplantımız bugün saat 14:00."))],
            headers=[
                {"name": "Subject", "value": "=?UTF-8?B?QcSfdXN0b3MgZGXEn2VybGVuZGlybWVzaQ==?="},
                {"name": "From", "value": "=?UTF-8?Q?G=C3=B6nderen?= <sender@example.test>"},
                {"name": "Date", "value": "Wed, 19 Aug 2026 10:00:00 +0300"},
                {"name": "Message-ID", "value": "<m1@example.test>"},
            ],
        )
        record = self.normalize(message(root))
        self.assertEqual(record["subject"], "Ağustos değerlendirmesi")
        self.assertIn("Gönderen", record["sender"])
        self.assertIn("Toplantımız", record["content"])
        self.assertEqual(record["received_date"], date(2026, 8, 19))

    def test_multipart_alternative_plain_wins(self):
        root = payload(parts=[
            payload(mime_type="text/html", data=b64("<p>HTML body</p>")),
            payload(mime_type="text/plain", data=b64("Plain body")),
        ])
        self.assertEqual(self.normalize(message(root))["content"], "Plain body")

    def test_html_only_preserves_lines(self):
        root = payload(parts=[payload(
            mime_type="text/html", data=b64("<div>Satır bir</div><div>Saat 14:00</div>")
        )])
        self.assertEqual(self.normalize(message(root))["content"], "Satır bir\nSaat 14:00")

    def test_nested_multipart_mixed(self):
        root = payload(parts=[payload(parts=[payload(
            mime_type="text/plain", data=b64("Nested text")
        )], mime_type="multipart/alternative")])
        self.assertEqual(self.normalize(message(root))["content"], "Nested text")

    def test_selected_body_attachment_id_is_loaded(self):
        part = payload(mime_type="text/plain")
        part["body"] = {"attachmentId": "body-1"}
        record = self.normalize(
            message(payload(parts=[part])), {"body-1": {"data": b64("Harici gövde")}}
        )
        self.assertEqual(record["content"], "Harici gövde")
        self.assertEqual(self.attachment_calls, [("m1", "body-1")])

    def test_calendar_metadata_selectively_loads_raw(self):
        for part in (
            payload(mime_type="text/calendar", data=b64("BEGIN:VCALENDAR")),
            payload(mime_type="application/octet-stream", filename="invite.ICS"),
        ):
            with self.subTest(part=part):
                record = self.normalize(message(payload(parts=[part])))
                self.assertTrue(record["raw_source"])
                self.assertEqual(self.raw_calls, ["m1"])

    def test_inline_calendar_marker_loads_raw(self):
        root = payload(parts=[payload(mime_type="text/plain", data=b64("BEGIN:VCALENDAR\nBEGIN:VEVENT"))])
        self.assertTrue(self.normalize(message(root))["raw_source"])
        self.assertEqual(self.raw_calls, ["m1"])

    def test_ordinary_message_makes_no_raw_call(self):
        root = payload(parts=[payload(mime_type="text/plain", data=b64("ordinary"))])
        self.assertEqual(self.normalize(message(root))["raw_source"], "")
        self.assertEqual(self.raw_calls, [])

    def test_malformed_base64_becomes_degraded_record(self):
        root = payload(parts=[payload(mime_type="text/plain", data="%%%")])
        record = self.normalize(message(root))
        self.assertEqual(record["content"], "")
        self.assertEqual(record["source_message_id"], "gmail:m1")

    def test_malformed_headers_do_not_drop_structurally_valid_message(self):
        root = payload(data=b64("body"), mime_type="text/plain")
        root["headers"] = "not-a-header-list"
        record = self.normalize(message(root))
        self.assertEqual(record["content"], "body")
        self.assertEqual(record["source_message_id"], "gmail:m1")

    def test_malformed_plain_falls_back_to_valid_html(self):
        root = payload(parts=[
            payload(mime_type="text/plain", data="%%%"),
            payload(mime_type="text/html", data=b64("<p>Fallback body</p>")),
        ])
        self.assertEqual(self.normalize(message(root))["content"], "Fallback body")

    def test_required_body_or_raw_api_failure_is_not_degraded(self):
        body_part = payload(mime_type="text/plain")
        body_part["body"] = {"attachmentId": "body"}
        with self.assertRaises(GmailApiError):
            normalize_message(
                message(payload(parts=[body_part])),
                lambda *_: (_ for _ in ()).throw(GmailApiError("network")),
                lambda *_: {},
            )

        calendar = payload(parts=[payload(mime_type="text/calendar", data=b64("BEGIN:VCALENDAR"))])
        with self.assertRaises(GmailApiError):
            normalize_message(
                message(calendar),
                lambda *_: {},
                lambda *_: (_ for _ in ()).throw(GmailApiError("network")),
            )

    def test_missing_message_id_uses_no_empty_collision(self):
        value = message(payload())
        del value["id"]
        with self.assertRaises(MessageStructureError):
            self.normalize(value)

    def test_missing_rfc_message_id_has_stable_gmail_fallback(self):
        first = self.normalize(message(payload(), message_id="abc"))
        second = self.normalize(message(payload(), message_id="abc"))
        self.assertEqual(first["source_message_id"], "gmail:abc")
        self.assertEqual(first["source_message_id"], second["source_message_id"])

    def test_neutral_subject_body_only_meeting_reaches_real_parser(self):
        root = payload(
            parts=[payload(mime_type="text/plain", data=b64(
                "Microsoft Teams toplantımız bugün saat 14:00'te yapılacaktır."
            ))],
            headers=[
                {"name": "Subject", "value": "Ağustos değerlendirmesi"},
                {"name": "Date", "value": "Wed, 19 Aug 2026 10:00:00 +0300"},
            ],
        )
        record = self.normalize(message(root))
        meetings = extract_meetings(record, date(2026, 8, 19))
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["time"], "14:00")

    def test_critical_internal_date_or_labels_failure_aborts(self):
        for value in (
            message(payload(), internal_date="not-a-number"),
            message(payload(), labels="INBOX"),
        ):
            with self.subTest(value=value), self.assertRaises(MessageStructureError):
                self.normalize(value)


if __name__ == "__main__":
    unittest.main()
