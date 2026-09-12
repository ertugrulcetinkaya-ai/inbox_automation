import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mail_digest.config import GMAIL_SCOPES
from mail_digest.sources.gmail.auth import GmailAuthError, load_credentials, write_private_text


class GmailAuthTests(unittest.TestCase):
    def test_only_readonly_scope_is_configured(self):
        self.assertEqual(GMAIL_SCOPES, ("https://www.googleapis.com/auth/gmail.readonly",))

    @patch("mail_digest.sources.gmail.auth.Credentials.from_authorized_user_file")
    def test_valid_cached_token(self, loader):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.json"
            path.write_text("{}")
            credentials = Mock(valid=True, expired=False)
            loader.return_value = credentials
            self.assertIs(load_credentials(path), credentials)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @patch("mail_digest.sources.gmail.auth.Request")
    @patch("mail_digest.sources.gmail.auth.Credentials.from_authorized_user_file")
    def test_expired_token_refreshes_and_is_rewritten_privately(self, loader, request):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.json"
            path.write_text("{}")
            credentials = Mock(expired=True, refresh_token="refresh", valid=True)
            credentials.to_json.return_value = '{"token":"redacted"}'
            loader.return_value = credentials
            load_credentials(path)
            credentials.refresh.assert_called_once_with(request.return_value)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @patch("mail_digest.sources.gmail.auth.Credentials.from_authorized_user_file")
    def test_refresh_failure_is_actionable(self, loader):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.json"
            path.write_text("{}")
            credentials = Mock(expired=True, refresh_token="refresh")
            credentials.refresh.side_effect = RuntimeError("revoked")
            loader.return_value = credentials
            with self.assertRaisesRegex(GmailAuthError, "gmail_auth.py"):
                load_credentials(path)

    def test_missing_token_never_imports_or_starts_browser_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GmailAuthError, "gmail_auth.py"):
                load_credentials(Path(directory) / "missing.json")

    def test_private_writer_uses_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "token.json"
            write_private_text(path, "secret")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    @patch("mail_digest.sources.gmail.auth.build")
    @patch("mail_digest.sources.gmail.auth.AuthorizedHttp")
    @patch("mail_digest.sources.gmail.auth.httplib2.Http")
    @patch("mail_digest.sources.gmail.auth.load_credentials")
    def test_build_service_uses_an_explicit_http_deadline(
        self,
        load_credentials_mock,
        http_mock,
        authorized_http_mock,
        build_mock,
    ):
        from mail_digest.config import GMAIL_HTTP_TIMEOUT_SECONDS
        from mail_digest.sources.gmail.auth import build_service

        credentials = Mock()
        load_credentials_mock.return_value = credentials

        build_service()

        http_mock.assert_called_once_with(timeout=GMAIL_HTTP_TIMEOUT_SECONDS)
        authorized_http_mock.assert_called_once_with(
            credentials,
            http=http_mock.return_value,
        )
        build_mock.assert_called_once_with(
            "gmail",
            "v1",
            http=authorized_http_mock.return_value,
            cache_discovery=False,
        )

    def test_interactive_flow_exists_only_in_explicit_module(self):
        root = Path(__file__).resolve().parents[1] / "mail_digest" / "sources" / "gmail"
        auth_source = (root / "auth.py").read_text()
        interactive_source = (root / "interactive.py").read_text()
        self.assertNotIn("InstalledAppFlow", auth_source)
        self.assertIn("run_local_server(port=0)", interactive_source)


if __name__ == "__main__":
    unittest.main()
