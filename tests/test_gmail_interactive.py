import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mail_digest.sources.gmail.auth import GmailAuthError
from mail_digest.sources.gmail.interactive import authorize_interactively


class GmailInteractiveAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"MAIL_DIGEST_TARGET_EMAIL": "user@example.com"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _authorization_mocks(self, profile):
        flow = Mock()
        flow.run_local_server.return_value.to_json.return_value = "synthetic token"
        service = Mock()
        service.users.return_value.getProfile.return_value.execute.return_value = profile
        return flow, service

    def test_authorization_accepts_matching_profile_and_writes_token(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            credentials_path.write_text("{}", encoding="utf-8")
            flow, service = self._authorization_mocks(
                {"emailAddress": "USER@example.com"}
            )
            with patch(
                "mail_digest.sources.gmail.interactive.InstalledAppFlow.from_client_secrets_file",
                return_value=flow,
            ), patch(
                "mail_digest.sources.gmail.interactive.build", return_value=service
            ), patch(
                "mail_digest.sources.gmail.interactive.write_private_text"
            ) as writer:
                token_path = authorize_interactively(
                    credentials_path, Path(directory) / "token.json"
                )

            writer.assert_called_once_with(token_path, "synthetic token")

    def test_authorization_rejects_mismatched_profile_before_writing_token(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            credentials_path.write_text("{}", encoding="utf-8")
            flow, service = self._authorization_mocks(
                {"emailAddress": "other@example.org"}
            )
            with patch(
                "mail_digest.sources.gmail.interactive.InstalledAppFlow.from_client_secrets_file",
                return_value=flow,
            ), patch(
                "mail_digest.sources.gmail.interactive.build", return_value=service
            ), patch(
                "mail_digest.sources.gmail.interactive.write_private_text"
            ) as writer:
                with self.assertRaisesRegex(GmailAuthError, "Gmail account mismatch"):
                    authorize_interactively(
                        credentials_path, Path(directory) / "token.json"
                    )

            writer.assert_not_called()
