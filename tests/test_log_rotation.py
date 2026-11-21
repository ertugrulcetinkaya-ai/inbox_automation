import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import plistlib
from unittest.mock import patch

from mail_digest.config import PROJECT_ROOT
from mail_digest.services.log_rotation import rotate_logs


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

            self.assertFalse(log_path.exists())
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

    def test_rotation_errors_are_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "listener.log"
            log_path.write_text("oversized", encoding="utf-8")
            output = StringIO()

            with patch.object(Path, "replace", side_effect=OSError("fixture error")), redirect_stdout(output):
                rotate_logs(directory, max_bytes=1)

            self.assertEqual(output.getvalue(), "")
            self.assertTrue(log_path.exists())

    def test_cli_main_rotates_project_logs_before_running_digest(self):
        from mail_digest.cli import main

        with patch("mail_digest.cli.rotate_logs") as rotate, patch(
            "mail_digest.cli.run_digest", return_value=0
        ), patch("sys.argv", ["main.py"]):
            self.assertEqual(main(), 0)

        rotate.assert_called_once_with(PROJECT_ROOT / "logs")

    def test_listener_main_rotates_project_logs_before_loading_credentials(self):
        from telegram_listener import main

        output = StringIO()
        with patch("telegram_listener.rotate_logs") as rotate, patch(
            "telegram_listener.load_env", return_value={}
        ), redirect_stdout(output):
            main()

        rotate.assert_called_once_with(PROJECT_ROOT / "logs")
        self.assertIn("Missing TELEGRAM_BOT_TOKEN", output.getvalue())

    def test_listener_plist_has_throttle_interval(self):
        from scripts.install_launchd import TEMPLATES, render_template

        rendered = plistlib.loads(render_template(TEMPLATES["listener"]))

        self.assertEqual(rendered["ThrottleInterval"], 30)


if __name__ == "__main__":
    unittest.main()
