import subprocess
import signal
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from main import FIELD_DELIMITER
from mail_digest.config import load_env
from mail_digest.delivery.telegram import send_telegram
from mail_digest.sources.apple_mail import fetch_mail
from telegram_listener import send_message as listener_send_message


class EnvironmentLoaderTests(unittest.TestCase):
    def test_loader_ignores_comments_malformed_lines_and_empty_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telegram.env"
            path.write_text(
                "  # comment\n"
                "TELEGRAM_BOT_TOKEN = 'fixture-token'\n"
                "malformed\n"
                " = ignored\n"
                'TELEGRAM_CHAT_ID="fixture-chat"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                load_env(path),
                {
                    "TELEGRAM_BOT_TOKEN": "fixture-token",
                    "TELEGRAM_CHAT_ID": "fixture-chat",
                },
            )

    def test_missing_env_file_error_contains_only_the_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.env"
            with self.assertRaisesRegex(FileNotFoundError, "missing.env"):
                load_env(path)


class AppleMailSourceFailureTests(unittest.TestCase):
    def completed(self, stdout="", stderr="", returncode=0):
        return subprocess.CompletedProcess(
            args=["osascript"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @patch("mail_digest.sources.apple_mail._run_applescript")
    def test_empty_mail_database_returns_empty_records(self, run):
        run.return_value = self.completed()

        self.assertEqual(fetch_mail(), [])

    @patch("mail_digest.sources.apple_mail._run_applescript")
    def test_corrupt_mail_output_is_ignored(self, run):
        run.return_value = self.completed("not a mail record\ninvalid" + FIELD_DELIMITER + "short")

        self.assertEqual(fetch_mail(), [])

    @patch("mail_digest.sources.apple_mail._run_applescript")
    def test_nonzero_applescript_exit_returns_none(self, run):
        run.return_value = self.completed(stderr="Mail database unavailable", returncode=1)

        self.assertIsNone(fetch_mail())

    @patch("mail_digest.sources.apple_mail._run_applescript", return_value=None)
    def test_applescript_timeout_returns_none(self, run):
        del run

        self.assertIsNone(fetch_mail())

    @patch("mail_digest.sources.apple_mail.os.killpg")
    @patch("mail_digest.sources.apple_mail.subprocess.Popen")
    def test_applescript_timeout_kills_the_whole_process_group(self, popen, killpg):
        process = popen.return_value
        process.pid = 4321
        process.args = ["osascript", "mail_fetcher.applescript"]
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=process.args, timeout=180),
            ("", ""),
        ]

        from mail_digest.sources.apple_mail import _run_applescript

        self.assertIsNone(_run_applescript())
        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(process.pid, signal.SIGKILL)

    @patch("mail_digest.sources.apple_mail._run_applescript")
    def test_transport_record_is_sanitized_and_parsed(self, run):
        record = FIELD_DELIMITER.join([
            "Account",
            "Inbox",
            "Fixture Sender <sender@example.com>",
            "Meeting",
            "Wednesday, August 12, 2026 at 09:00:00",
            "Line one__MAIL_DIGEST_LINEBREAK__Line two",
            "<fixture@example.com>",
            "Content-Type: text/calendar\nBEGIN:VCALENDAR",
        ])
        run.return_value = self.completed(record)

        records = fetch_mail()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["content"], "Line one\nLine two")
        self.assertEqual(records[0]["source_message_id"], "<fixture@example.com>")


class TelegramDeliveryFailureTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "test-chat",
        }

    @patch("mail_digest.delivery.telegram.requests.post")
    @patch("mail_digest.delivery.telegram.load_env")
    def test_http_error_is_failure(self, load_env, post):
        load_env.return_value = self.env
        post.return_value = Mock(status_code=500, json=lambda: {"ok": False})

        self.assertFalse(send_telegram("test"))

    @patch("mail_digest.delivery.telegram.requests.post")
    @patch("mail_digest.delivery.telegram.load_env")
    def test_http_200_with_telegram_error_is_failure(self, load_env, post):
        load_env.return_value = self.env
        post.return_value = Mock(status_code=200, json=lambda: {"ok": False, "error_code": 400})

        self.assertFalse(send_telegram("test"))

    @patch("mail_digest.delivery.telegram.requests.post")
    @patch("mail_digest.delivery.telegram.load_env")
    def test_invalid_json_response_is_failure(self, load_env, post):
        load_env.return_value = self.env
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("invalid json")
        post.return_value = response

        self.assertFalse(send_telegram("test"))

    @patch("mail_digest.delivery.telegram.requests.post")
    @patch("mail_digest.delivery.telegram.load_env")
    def test_successful_telegram_response_is_success(self, load_env, post):
        load_env.return_value = self.env
        post.return_value = Mock(status_code=200, json=lambda: {"ok": True, "result": {}})

        self.assertTrue(send_telegram("test"))

    @patch("mail_digest.delivery.telegram.requests.post")
    @patch("mail_digest.delivery.telegram.load_env")
    def test_long_message_is_split_and_all_chunks_must_succeed(self, load_env, post):
        load_env.return_value = self.env
        post.return_value = Mock(status_code=200, json=lambda: {"ok": True})

        self.assertTrue(send_telegram("x" * 7001))
        self.assertEqual(post.call_count, 3)

    @patch("mail_digest.delivery.telegram.requests.post")
    @patch("mail_digest.delivery.telegram.load_env")
    def test_network_error_redacts_bot_token(self, load_env, post):
        token = self.env["TELEGRAM_BOT_TOKEN"]
        load_env.return_value = self.env
        post.side_effect = RuntimeError(f"request failed for bot{token}/sendMessage")
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertFalse(send_telegram("test"))

        self.assertNotIn(token, output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())


class TelegramListenerDeliveryFailureTests(unittest.TestCase):
    @patch("telegram_listener.requests.post")
    def test_http_or_telegram_error_is_failure(self, post):
        post.return_value = Mock(status_code=500)
        self.assertFalse(listener_send_message("fixture-token", "fixture-chat", "test"))

        post.return_value = Mock(status_code=200, json=lambda: {"ok": False})
        self.assertFalse(listener_send_message("fixture-token", "fixture-chat", "test"))

    @patch("telegram_listener.requests.post")
    def test_successful_response_is_success(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {"ok": True})

        self.assertTrue(listener_send_message("fixture-token", "fixture-chat", "test"))

    @patch("telegram_listener.requests.post")
    def test_network_error_redacts_bot_token(self, post):
        token = "fixture-secret-token"
        post.side_effect = RuntimeError(f"request failed for bot{token}/sendMessage")
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertFalse(listener_send_message(token, "fixture-chat", "test"))

        self.assertNotIn(token, output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())


if __name__ == "__main__":
    unittest.main()
