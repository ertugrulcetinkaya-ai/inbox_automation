import os
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_digest.cli import main, run_digest
from mail_digest.services.lock import DigestAlreadyRunning, digest_lock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DigestLockTests(unittest.TestCase):
    def test_zero_wait_keeps_immediate_contention_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"
            with digest_lock(lock_path):
                started = time.monotonic()
                with self.assertRaises(DigestAlreadyRunning):
                    with digest_lock(lock_path, wait_seconds=0):
                        self.fail("busy lock was acquired")
                self.assertLess(time.monotonic() - started, 0.5)

    def test_waiting_caller_acquires_lock_after_it_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"
            acquired = threading.Event()
            errors = []

            def waiting_caller():
                try:
                    with digest_lock(lock_path, wait_seconds=2):
                        acquired.set()
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            with digest_lock(lock_path):
                thread = threading.Thread(target=waiting_caller)
                thread.start()
                time.sleep(0.6)

            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(acquired.is_set())
            self.assertEqual(errors, [])

    def test_waiting_caller_times_out(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"
            with digest_lock(lock_path):
                started = time.monotonic()
                with self.assertRaises(DigestAlreadyRunning):
                    with digest_lock(lock_path, wait_seconds=0.55):
                        self.fail("busy lock was acquired")
                self.assertGreaterEqual(time.monotonic() - started, 0.5)

    @patch("mail_digest.cli._run_digest", return_value=0)
    def test_scheduled_digest_waits_for_lock_and_records_separately(self, _run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "digest.lock"
            status_path = root / "status.json"
            results = []

            def scheduled_caller():
                results.append(run_digest(scheduled=True))

            with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch(
                "mail_digest.cli.daily_lock_wait_seconds", return_value=2.0
            ), patch.dict(os.environ, {"MAIL_DIGEST_STATUS_FILE": str(status_path)}):
                with digest_lock(lock_path):
                    thread = threading.Thread(target=scheduled_caller)
                    thread.start()
                    time.sleep(0.6)

                thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(results, [0])
            modes = json.loads(status_path.read_text(encoding="utf-8"))["modes"]
            self.assertIn("daily_scheduled", modes)
            self.assertNotIn("daily", modes)
            self.assertIsNotNone(modes["daily_scheduled"]["last_success_at"])

    @patch("mail_digest.cli._run_digest", return_value=0)
    def test_scheduled_digest_timeout_records_skipped_lock_and_exit_75(self, _run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "digest.lock"
            status_path = root / "status.json"
            with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch(
                "mail_digest.cli.daily_lock_wait_seconds", return_value=0
            ), patch.dict(os.environ, {"MAIL_DIGEST_STATUS_FILE": str(status_path)}):
                with digest_lock(lock_path):
                    self.assertEqual(run_digest(scheduled=True), 75)

            entry = json.loads(status_path.read_text(encoding="utf-8"))["modes"][
                "daily_scheduled"
            ]
            self.assertEqual(entry["last_error_type"], "skipped_lock")
            self.assertEqual(entry["last_skipped_lock_at"], entry["last_failure_at"])
            self.assertIsNone(entry["last_started_at"])
            _run.assert_not_called()

    def test_leftover_lock_file_does_not_block_a_new_run(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"
            lock_path.write_text("stale process marker", encoding="utf-8")

            with digest_lock(lock_path):
                self.assertTrue(lock_path.exists())

    def test_lock_is_released_after_an_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"

            with self.assertRaises(RuntimeError):
                with digest_lock(lock_path):
                    raise RuntimeError("digest failed")

            with digest_lock(lock_path):
                pass

    def test_another_process_cannot_acquire_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"
            child_code = """
from mail_digest.services.lock import DigestAlreadyRunning, digest_lock
import sys

try:
    with digest_lock(sys.argv[1]):
        print('acquired')
except DigestAlreadyRunning:
    print('busy')
"""
            child_env = os.environ.copy()
            child_env["PYTHONPATH"] = os.pathsep.join(
                [str(PROJECT_ROOT), child_env.get("PYTHONPATH", "")]
            )

            with digest_lock(lock_path):
                result = subprocess.run(
                    [sys.executable, "-c", child_code, str(lock_path)],
                    capture_output=True,
                    check=True,
                    text=True,
                    env=child_env,
                )

            self.assertEqual(result.stdout.strip(), "busy")

    @patch("mail_digest.cli._run_digest", return_value=0)
    def test_cli_run_digest_uses_the_shared_lock(self, run):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "digest.lock"
            status_path = Path(directory) / "status.json"
            with patch.dict(os.environ, {"MAIL_DIGEST_LOCK_FILE": str(lock_path)}), patch.dict(
                os.environ,
                {"MAIL_DIGEST_STATUS_FILE": str(status_path)},
            ):
                self.assertEqual(run_digest(upcoming=True), 0)

        run.assert_called_once_with(upcoming=True, dry_run=False)

    @patch("mail_digest.cli.digest_lock")
    @patch("mail_digest.cli._run_digest", return_value=0)
    def test_cli_passes_explicit_lock_wait_to_shared_lock(self, run, lock):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MAIL_DIGEST_STATUS_FILE": str(Path(directory) / "status.json")},
        ):
            run_digest(wait_seconds=1.5)

        lock.assert_called_once_with(wait_seconds=1.5)

    @patch("mail_digest.cli.run_digest", side_effect=DigestAlreadyRunning)
    def test_cli_contention_is_a_successful_noop_for_launchd(self, run):
        with patch.object(sys, "argv", ["main.py"]):
            self.assertEqual(main(), 0)

    @patch("mail_digest.cli.run_digest", return_value=0)
    def test_cli_routes_week_attention_and_reminder_modes(self, run):
        for flag, mode in (
            ("--week", "weekly"),
            ("--attention", "attention"),
            ("--reminder", "reminder"),
        ):
            with self.subTest(flag=flag), patch.object(
                sys,
                "argv",
                ["main.py", flag, "--dry-run"],
            ):
                self.assertEqual(main(), 0)
                run.assert_called_once_with(dry_run=True, mode=mode)
                run.reset_mock()

    @patch("mail_digest.cli.rotate_logs")
    @patch("mail_digest.cli.run_digest", return_value=0)
    def test_cli_routes_scheduled_daily_flag(self, run, _rotate):
        with patch.object(sys, "argv", ["main.py", "--scheduled"]):
            self.assertEqual(main(), 0)

        run.assert_called_once_with(
            upcoming=False,
            dry_run=False,
            scheduled=True,
        )


class TelegramListenerLockIntegrationTests(unittest.TestCase):
    @patch("telegram_listener.execute_digest", return_value=0)
    def test_listener_run_digest_preserves_success_response_contract(self, execute):
        from telegram_listener import run_digest as listener_run_digest

        self.assertEqual(
            listener_run_digest(),
            "Bugünün toplantı özeti Telegram sohbetinize gönderildi.",
        )
        execute.assert_called_once_with(upcoming=False, wait_seconds=30)

    @patch("telegram_listener.execute_digest", side_effect=DigestAlreadyRunning)
    def test_listener_reports_contention(self, execute):
        from telegram_listener import run_digest as listener_run_digest

        self.assertIn("hazırlanıyor", listener_run_digest())
        execute.assert_called_once_with(upcoming=False, wait_seconds=30)

    @patch("telegram_listener.execute_digest", return_value=0)
    def test_listener_routes_weekly_and_attention_modes_through_shared_service(self, execute):
        from telegram_listener import run_digest as listener_run_digest

        self.assertEqual(
            listener_run_digest(mode="weekly"),
            "Haftalık toplantı özeti Telegram sohbetinize gönderildi.",
        )
        execute.assert_called_once_with(mode="weekly", wait_seconds=30)

        execute.reset_mock()
        self.assertEqual(
            listener_run_digest(mode="attention"),
            "Dikkat gerektiren toplantılar Telegram sohbetinize gönderildi.",
        )
        execute.assert_called_once_with(mode="attention", wait_seconds=30)

        execute.reset_mock()
        self.assertEqual(
            listener_run_digest(upcoming=True),
            "Bugün ve sonraki toplantı özeti Telegram sohbetinize gönderildi.",
        )
        execute.assert_called_once_with(upcoming=True, wait_seconds=30)


if __name__ == "__main__":
    unittest.main()
