import os
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_digest.sources import MailSourceConfigurationError, fetch_mail, selected_source_name


class MailSourceDispatchTests(unittest.TestCase):
    @patch.dict(os.environ, {"MAIL_SOURCE": "gmail"})
    @patch("mail_digest.sources.gmail.fetch_mail", return_value=[{"source": "gmail"}])
    @patch("mail_digest.sources.apple_mail.fetch_mail")
    def test_gmail_source_is_selected_independently(self, apple, gmail):
        self.assertEqual(fetch_mail(), [{"source": "gmail"}])
        gmail.assert_called_once_with()
        apple.assert_not_called()

    @patch.dict(os.environ, {"MAIL_SOURCE": "apple_mail"})
    @patch("mail_digest.sources.apple_mail.fetch_mail", return_value=[{"source": "apple"}])
    @patch("mail_digest.sources.gmail.fetch_mail")
    def test_apple_source_is_selected(self, gmail, apple):
        self.assertEqual(fetch_mail(), [{"source": "apple"}])
        apple.assert_called_once_with()
        gmail.assert_not_called()

    @patch.dict(os.environ, {"MAIL_SOURCE": "gmail"})
    @patch("mail_digest.sources.gmail.fetch_mail", return_value=None)
    @patch("mail_digest.sources.apple_mail.fetch_mail")
    def test_gmail_failure_never_falls_back(self, apple, gmail):
        self.assertIsNone(fetch_mail())
        gmail.assert_called_once_with()
        apple.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_migration_default_remains_apple_mail(self):
        self.assertEqual(selected_source_name(), "apple_mail")

    @patch.dict(os.environ, {"MAIL_SOURCE": "unknown"})
    def test_unknown_source_fails_clearly(self):
        with self.assertRaisesRegex(MailSourceConfigurationError, "Unknown MAIL_SOURCE"):
            fetch_mail()


class GmailReadOnlyArchitectureTests(unittest.TestCase):
    def test_gmail_source_contains_no_write_api_invocations_or_scopes(self):
        source_root = Path(__file__).resolve().parents[1] / "mail_digest" / "sources" / "gmail"
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in source_root.glob("*.py")
        )
        for operation in ("modify", "batchModify", "trash", "untrash", "delete", "send", "insert", "import"):
            self.assertNotIn(f".{operation}(", combined)
        for scope in ("gmail.modify", "gmail.compose", "mail.google.com", "gmail.send", "gmail.labels"):
            self.assertNotIn(scope, combined)

    def test_launchd_renderer_defaults_to_apple_and_supports_gmail_paths(self):
        from scripts.install_launchd import TEMPLATES, render_template

        rendered = render_template(TEMPLATES["summary"]).decode()
        self.assertIn("<string>apple_mail</string>", rendered)
        self.assertIn("GMAIL_CREDENTIALS_FILE", rendered)
        self.assertNotIn("__GMAIL_", rendered)


if __name__ == "__main__":
    unittest.main()
