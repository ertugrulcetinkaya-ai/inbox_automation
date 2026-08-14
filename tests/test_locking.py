import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_digest.cli import main, run_digest
from mail_digest.services.lock import DigestAlreadyRunning, digest_lock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DigestLockTests(unittest.TestCase):
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
            with patch("mail_digest.services.lock.DIGEST_LOCK_FILE", lock_path):
                self.assertEqual(run_digest(upcoming=True), 0)

        run.assert_called_once_with(upcoming=True, dry_run=False)

    @patch("mail_digest.cli.run_digest", side_effect=DigestAlreadyRunning)
    def test_cli_contention_is_a_successful_noop_for_launchd(self, run):
        with patch.object(sys, "argv", ["main.py"]):
            self.assertEqual(main(), 0)


class TelegramListenerLockIntegrationTests(unittest.TestCase):
    @patch("telegram_listener.execute_digest", return_value=0)
    def test_listener_uses_the_shared_cli_service(self, execute):
        from telegram_listener import run_digest as listener_run_digest

        self.assertIn("gönderildi", listener_run_digest())
        execute.assert_called_once_with(upcoming=False)

    @patch("telegram_listener.execute_digest", side_effect=DigestAlreadyRunning)
    def test_listener_reports_contention(self, execute):
        from telegram_listener import run_digest as listener_run_digest

        self.assertIn("hazırlanıyor", listener_run_digest())
        execute.assert_called_once_with(upcoming=False)


if __name__ == "__main__":
    unittest.main()
