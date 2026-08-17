import subprocess
import signal
import unittest
from unittest.mock import Mock, patch

from main import FIELD_DELIMITER
from mail_digest.delivery.telegram import send_telegram
from mail_digest.sources.apple_mail import fetch_mail


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


if __name__ == "__main__":
    unittest.main()
