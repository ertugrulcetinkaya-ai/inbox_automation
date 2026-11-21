"""Privacy checks for Gmail source failure logs."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from googleapiclient.errors import HttpError

from mail_digest.sources.gmail import fetch_mail
from mail_digest.sources.gmail.api import GmailApi, GmailApiError


class HttpResponse(dict):
    status = 403
    reason = "Forbidden"


class FailingRequest:
    def __init__(self, error):
        self.error = error

    def execute(self):
        raise self.error


class DummyStore:
    def close(self):
        pass


class FailingSynchronizer:
    def __init__(self, error):
        self.error = error

    def sync(self):
        raise self.error


class GmailSourceLogTests(unittest.TestCase):
    def test_rate_limit_log_has_reason_without_message_id_or_api_url(self):
        response = HttpResponse()
        error = HttpError(
            response,
            json.dumps(
                {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
            ).encode(),
            uri=(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                "synthetic-message-id?format=full"
            ),
        )
        api = GmailApi(None, sleep=lambda _: None)
        with self.assertRaises(GmailApiError) as raised:
            api._execute(lambda: FailingRequest(error))

        store = DummyStore()
        with (
            patch("mail_digest.sources.gmail.build_service", return_value=object()),
            patch("mail_digest.sources.gmail.GmailApi", return_value=object()),
            patch("mail_digest.sources.gmail.GmailStore", return_value=store),
            patch(
                "mail_digest.sources.gmail.GmailSynchronizer",
                return_value=FailingSynchronizer(raised.exception),
            ),
            patch("mail_digest.sources.gmail.log") as logger,
        ):
            self.assertIsNone(fetch_mail())

        logger.assert_called_once()
        message = logger.call_args.args[0]
        self.assertIn("HTTP 403 rateLimitExceeded", message)
        self.assertIn("GmailApiError", message)
        self.assertNotIn("synthetic-message-id", message)
        self.assertNotIn("googleapis.com", message)


if __name__ == "__main__":
    unittest.main()
