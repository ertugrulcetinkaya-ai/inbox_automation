"""Offline tests for the shared Telegram bot token guard."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mail_digest.config import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import check_bot_token_isolation as guard  # noqa: E402

SCRIPT = PROJECT_ROOT / "scripts" / "check_bot_token_isolation.py"


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


class BotTokenIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.own_env = self.home / "inbox" / "telegram.env"
        write_private(self.own_env, "TELEGRAM_BOT_TOKEN=inbox-fixture\nTELEGRAM_CHAT_ID=1\n")

    def run_guard(self, extra_env=None):
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"TELEGRAM_BOT_TOKEN", "OTHER_TELEGRAM_ENV_FILES"}
        }
        env.update({"HOME": str(self.home), "TELEGRAM_ENV_FILE": str(self.own_env)})
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_separate_bots_pass(self):
        write_private(
            self.home / ".config" / "company_reporting_hub" / "telegram.env",
            "TELEGRAM_BOT_TOKEN=reports-fixture\n",
        )

        result = self.run_guard()

        self.assertEqual(result.returncode, guard.EXIT_OK, result.stderr)

    def test_missing_reporting_files_pass(self):
        self.assertEqual(self.run_guard().returncode, guard.EXIT_OK)

    def test_token_shared_with_reporting_env_fails_without_printing_it(self):
        write_private(
            self.home / ".config" / "company_reporting_hub" / "telegram.env",
            "TELEGRAM_BOT_TOKEN='inbox-fixture'\n",
        )

        result = self.run_guard()

        self.assertEqual(result.returncode, guard.EXIT_SHARED)
        self.assertNotIn("inbox-fixture", result.stdout + result.stderr)

    def test_token_shared_with_hermes_env_fails(self):
        write_private(self.home / ".hermes" / ".env", "export TELEGRAM_BOT_TOKEN=inbox-fixture\n")

        self.assertEqual(self.run_guard().returncode, guard.EXIT_SHARED)

    def test_extra_env_files_are_checked(self):
        extra = self.home / "other.env"
        write_private(extra, "TELEGRAM_BOT_TOKEN=inbox-fixture\n")

        result = self.run_guard({"OTHER_TELEGRAM_ENV_FILES": str(extra)})

        self.assertEqual(result.returncode, guard.EXIT_SHARED)

    def test_missing_own_token_is_reported(self):
        write_private(self.own_env, "TELEGRAM_CHAT_ID=1\n")

        self.assertEqual(self.run_guard().returncode, guard.EXIT_UNREADABLE)

    def test_read_token_ignores_comments_and_other_keys(self):
        path = self.home / "sample.env"
        write_private(path, "# TELEGRAM_BOT_TOKEN=commented\nTELEGRAM_CHAT_ID=2\n")

        self.assertEqual(guard.read_token(path), "")
        self.assertEqual(guard.read_token(self.home / "missing.env"), "")

    def test_unreadable_other_file_is_skipped_with_warning(self):
        stderr, stdout = StringIO(), StringIO()
        with patch.object(guard, "own_token", return_value="inbox-fixture"), patch.object(
            guard, "other_runtime_files", return_value=[Path("/unreadable")]
        ), patch.object(guard, "read_token", side_effect=PermissionError), redirect_stderr(
            stderr
        ), redirect_stdout(stdout):
            self.assertEqual(guard.main(), guard.EXIT_OK)
        self.assertIn("atlandı", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
