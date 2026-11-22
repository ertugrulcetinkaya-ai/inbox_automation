import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mail_digest.fileio import atomic_write_private


class AtomicPrivateFileTests(unittest.TestCase):
    def test_write_replaces_file_and_enforces_private_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"

            atomic_write_private(path, "synthetic state\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "synthetic state\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(list(path.parent.glob(".state.json.*")), [])

    def test_write_failure_preserves_existing_file_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("old state\n", encoding="utf-8")
            real_fdopen = os.fdopen

            def failing_fdopen(descriptor, *args, **kwargs):
                real_handle = real_fdopen(descriptor, *args, **kwargs)
                mock_handle = MagicMock()
                mock_handle.__enter__.return_value = mock_handle
                mock_handle.__exit__.side_effect = lambda *exc: real_handle.close()
                mock_handle.write.side_effect = OSError("synthetic write failure")
                mock_handle.fileno.return_value = real_handle.fileno()
                return mock_handle

            with patch("mail_digest.fileio.os.fdopen", side_effect=failing_fdopen):
                with self.assertRaisesRegex(OSError, "synthetic write failure"):
                    atomic_write_private(path, "replacement\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "old state\n")
            self.assertEqual(list(path.parent.glob(".state.json.*")), [])
