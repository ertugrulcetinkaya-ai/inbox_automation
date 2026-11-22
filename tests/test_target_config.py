import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_digest.config import TargetEmailConfigurationError, target_email


class TargetEmailConfigurationTests(unittest.TestCase):
    def test_environment_value_overrides_file_and_is_resolved_lazily(self):
        with patch.dict(
            os.environ,
            {
                "MAIL_DIGEST_TARGET_EMAIL": "user@example.com",
                "MAIL_DIGEST_TARGET_ENV_FILE": "/tmp/missing-target.env",
            },
        ):
            self.assertEqual(target_email(), "user@example.com")
            os.environ["MAIL_DIGEST_TARGET_EMAIL"] = "next@example.com"
            self.assertEqual(target_email(), "next@example.com")

    def test_private_target_env_file_has_one_required_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "target.env"
            path.write_text("MAIL_DIGEST_TARGET_EMAIL=user@example.com\n", encoding="utf-8")
            path.chmod(0o600)
            with patch.dict(
                os.environ,
                {
                    "MAIL_DIGEST_TARGET_EMAIL": "",
                    "MAIL_DIGEST_TARGET_ENV_FILE": str(path),
                },
            ):
                with self.assertRaises(TargetEmailConfigurationError):
                    target_email()

            with patch.dict(
                os.environ,
                {"MAIL_DIGEST_TARGET_ENV_FILE": str(path)},
                clear=True,
            ):
                self.assertEqual(target_email(), "user@example.com")

    def test_target_env_file_rejects_non_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "target.env"
            path.write_text("MAIL_DIGEST_TARGET_EMAIL=user@example.com\n", encoding="utf-8")
            path.chmod(0o640)
            with patch.dict(
                os.environ,
                {"MAIL_DIGEST_TARGET_ENV_FILE": str(path)},
                clear=True,
            ):
                with self.assertRaisesRegex(TargetEmailConfigurationError, "0600"):
                    target_email()

    def test_target_env_file_rejects_extra_lines_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "target.env"
            path.write_text(
                "MAIL_DIGEST_TARGET_EMAIL=user@example.com\nEXTRA=value\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            with patch.dict(
                os.environ,
                {"MAIL_DIGEST_TARGET_ENV_FILE": str(path)},
                clear=True,
            ):
                with self.assertRaisesRegex(TargetEmailConfigurationError, "exactly one"):
                    target_email()

            with patch.dict(
                os.environ,
                {"MAIL_DIGEST_TARGET_ENV_FILE": str(path.with_name("missing.env"))},
                clear=True,
            ):
                with self.assertRaisesRegex(TargetEmailConfigurationError, "missing"):
                    target_email()
