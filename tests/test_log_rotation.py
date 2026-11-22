import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import plistlib
from unittest.mock import patch

from mail_digest.config import PROJECT_ROOT
from mail_digest.services.log_rotation import (
    CLI_LOG_NAMES,
    DAILY_LOG_NAMES,
    LISTENER_LOG_NAMES,
    REMINDER_LOG_NAMES,
    log_names_for_mode,
    rotate_logs,
)


class LogRotationTests(unittest.TestCase):
    def test_oversized_logs_rotate_and_keep_only_requested_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "listener.log"
            log_path.write_text("current data", encoding="utf-8")
            Path(f"{log_path}.1").write_text("archive one", encoding="utf-8")
            Path(f"{log_path}.2").write_text("archive two", encoding="utf-8")
            Path(f"{log_path}.3").write_text("archive three", encoding="utf-8")
            Path(f"{log_path}.4").write_text("stale archive", encoding="utf-8")

            rotate_logs(directory, max_bytes=1, keep=3)

            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.read_text(encoding="utf-8"), "")
            self.assertEqual(Path(f"{log_path}.1").read_text(encoding="utf-8"), "current data")
            self.assertEqual(Path(f"{log_path}.2").read_text(encoding="utf-8"), "archive one")
            self.assertEqual(Path(f"{log_path}.3").read_text(encoding="utf-8"), "archive two")
            self.assertFalse(Path(f"{log_path}.4").exists())

    def test_small_logs_are_not_rotated(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "listener.log"
            log_path.write_text("small", encoding="utf-8")

            rotate_logs(directory, max_bytes=5)

            self.assertTrue(log_path.exists())
            self.assertFalse(Path(f"{log_path}.1").exists())

    def test_named_rotation_keeps_cli_and_listener_logs_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            all_names = (*CLI_LOG_NAMES, *LISTENER_LOG_NAMES)
            paths = {name: Path(directory) / name for name in all_names}
            for path in paths.values():
                path.write_text("oversized", encoding="utf-8")

            rotate_logs(directory, names=CLI_LOG_NAMES, max_bytes=1)

            for name in CLI_LOG_NAMES:
                self.assertTrue(paths[name].exists())
                self.assertEqual(paths[name].read_text(encoding="utf-8"), "")
                self.assertTrue(Path(f"{paths[name]}.1").exists())
            for name in LISTENER_LOG_NAMES:
                self.assertTrue(paths[name].exists())
                self.assertFalse(Path(f"{paths[name]}.1").exists())

            rotate_logs(directory, names=LISTENER_LOG_NAMES, max_bytes=1)

            for name in LISTENER_LOG_NAMES:
                self.assertTrue(paths[name].exists())
                self.assertEqual(paths[name].read_text(encoding="utf-8"), "")
                self.assertTrue(Path(f"{paths[name]}.1").exists())

    def test_rotation_preserves_open_append_descriptor(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "listener.log"
            log_path.write_text("before rotation", encoding="utf-8")
            descriptor = os.open(log_path, os.O_WRONLY | os.O_APPEND)
            try:
                original_inode = os.fstat(descriptor).st_ino
                rotate_logs(directory, names=(log_path.name,), max_bytes=1)
                os.write(descriptor, b"after rotation")
                self.assertEqual(os.fstat(descriptor).st_ino, original_inode)
                self.assertEqual(log_path.stat().st_ino, original_inode)
            finally:
                os.close(descriptor)

            self.assertEqual(log_path.read_text(encoding="utf-8"), "after rotation")
            self.assertEqual(
                Path(f"{log_path}.1").read_text(encoding="utf-8"), "before rotation"
            )

    def test_configured_log_names_match_launchd_templates(self):
        from scripts.install_launchd import TEMPLATES, render_template

        cli_names = set()
        for template_name in ("summary", "reminder"):
            rendered = plistlib.loads(
                render_template(
                    TEMPLATES[template_name], target_account="user@example.com"
                )
            )
            cli_names.add(Path(rendered["StandardOutPath"]).name)
            cli_names.add(Path(rendered["StandardErrorPath"]).name)
        listener = plistlib.loads(
            render_template(
                TEMPLATES["listener"], target_account="user@example.com"
            )
        )
        listener_names = {
            Path(listener["StandardOutPath"]).name,
            Path(listener["StandardErrorPath"]).name,
        }

        self.assertEqual(set(CLI_LOG_NAMES), cli_names)
        self.assertEqual(set(LISTENER_LOG_NAMES), listener_names)

    def test_rotation_errors_are_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "listener.log"
            log_path.write_text("oversized", encoding="utf-8")
            output = StringIO()

            with patch(
                "mail_digest.services.log_rotation.os.truncate",
                side_effect=OSError("fixture error"),
            ), redirect_stdout(output):
                rotate_logs(directory, max_bytes=1)

            self.assertEqual(output.getvalue(), "")
            self.assertTrue(log_path.exists())

    def test_digest_log_names_are_selected_by_mode(self):
        self.assertEqual(log_names_for_mode("daily_scheduled"), DAILY_LOG_NAMES)
        self.assertEqual(log_names_for_mode("reminder"), REMINDER_LOG_NAMES)

    def test_run_digest_rotates_mode_logs_after_lock(self):
        from contextlib import contextmanager

        from mail_digest.cli import run_digest

        events = []

        @contextmanager
        def fake_lock(*, wait_seconds):
            events.append(("lock", wait_seconds))
            yield

        def fake_rotate(path, *, names):
            events.append(("rotate", path, names))

        with patch("mail_digest.cli.digest_lock", side_effect=fake_lock), patch(
            "mail_digest.cli.rotate_logs", side_effect=fake_rotate
        ), patch("mail_digest.cli._run_digest", return_value=0) as digest:
            self.assertEqual(run_digest(mode="reminder", dry_run=True), 0)

        self.assertEqual(events[0][0], "lock")
        self.assertEqual(
            events[1], ("rotate", PROJECT_ROOT / "logs", REMINDER_LOG_NAMES)
        )
        digest.assert_called_once_with(upcoming=False, dry_run=True, mode="reminder")

    def test_listener_main_rotates_project_logs_before_loading_credentials(self):
        from telegram_listener import main

        output = StringIO()
        with patch("telegram_listener.rotate_logs") as rotate, patch(
            "telegram_listener.load_env", return_value={}
        ), redirect_stdout(output):
            self.assertEqual(main(), 1)

        rotate.assert_called_once_with(
            PROJECT_ROOT / "logs", names=LISTENER_LOG_NAMES
        )
        self.assertIn("Missing TELEGRAM_BOT_TOKEN", output.getvalue())

    def test_listener_plist_has_throttle_interval(self):
        from scripts.install_launchd import TEMPLATES, render_template

        rendered = plistlib.loads(
            render_template(
                TEMPLATES["listener"], target_account="user@example.com"
            )
        )

        self.assertEqual(rendered["ThrottleInterval"], 30)


if __name__ == "__main__":
    unittest.main()
