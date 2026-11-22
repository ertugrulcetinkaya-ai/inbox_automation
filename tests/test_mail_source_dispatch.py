import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
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
    def test_default_source_is_gmail(self):
        self.assertEqual(selected_source_name(), "gmail")

    @patch.dict(os.environ, {}, clear=True)
    @patch("mail_digest.sources.gmail.fetch_mail", return_value=[{"source": "gmail"}])
    @patch("mail_digest.sources.apple_mail.fetch_mail")
    def test_unset_source_uses_gmail_without_apple_mail_fallback(self, apple, gmail):
        self.assertEqual(fetch_mail(), [{"source": "gmail"}])
        gmail.assert_called_once_with()
        apple.assert_not_called()

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

    def test_launchd_renderer_defaults_to_gmail_and_supports_gmail_paths(self):
        from scripts.install_launchd import TEMPLATES, render_template

        rendered = render_template(
            TEMPLATES["summary"], target_account="user@example.com"
        ).decode()
        self.assertIn("<string>gmail</string>", rendered)
        self.assertIn("GMAIL_CREDENTIALS_FILE", rendered)
        self.assertNotIn("__GMAIL_", rendered)
        self.assertIn("<string>user@example.com</string>", rendered)

        apple_rendered = render_template(
            TEMPLATES["summary"],
            mail_source="apple_mail",
            target_account="user@example.com",
        ).decode()
        self.assertIn("<string>apple_mail</string>", apple_rendered)

        reminder = render_template(
            TEMPLATES["reminder"],
            reminder_minutes=30,
            target_account="user@example.com",
        ).decode()
        self.assertIn("<string>--reminder</string>", reminder)
        self.assertIn("<string>30</string>", reminder)
        self.assertIn("<integer>300</integer>", reminder)

    def test_all_launchd_templates_receive_target_account(self):
        import plistlib

        from scripts.install_launchd import TEMPLATES, render_template

        for template_name, template_path in TEMPLATES.items():
            with self.subTest(template=template_name):
                rendered = plistlib.loads(
                    render_template(
                        template_path, target_account="user@example.com"
                    )
                )
                self.assertEqual(
                    rendered["EnvironmentVariables"]["MAIL_DIGEST_TARGET_EMAIL"],
                    "user@example.com",
                )

    def test_installer_reports_missing_target_without_printing_an_address(self):
        from scripts.install_launchd import main as install_main

        output = StringIO()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MAIL_DIGEST_TARGET_ENV_FILE": str(Path(directory) / "missing.env")},
            clear=True,
        ), patch.object(sys, "argv", ["install_launchd.py"]), redirect_stderr(output):
            with self.assertRaises(SystemExit) as raised:
                install_main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("MAIL_DIGEST_TARGET_EMAIL ayarlayın", output.getvalue())
        self.assertNotIn("@", output.getvalue())


if __name__ == "__main__":
    unittest.main()
