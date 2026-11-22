import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from mail_digest.services.reminder_state import load_reminder_state


class ReminderStateTests(unittest.TestCase):
    def test_malformed_json_is_logged_by_exception_type_and_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reminders.json"
            path.write_text("{synthetic malformed", encoding="utf-8")

            with patch("mail_digest.services.reminder_state.log") as log:
                state = load_reminder_state(path, now=datetime(2026, 11, 22, 12))

            self.assertEqual(state, {"sent": {}, "last_successful_reminder_scan": None})
            self.assertFalse(path.exists())
            self.assertTrue(path.with_name("reminders.json.corrupt").exists())
            log.assert_called_once()
            self.assertIn("JSONDecodeError", log.call_args.args[0])
            self.assertNotIn("synthetic malformed", log.call_args.args[0])

    def test_missing_state_remains_a_normal_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(
                load_reminder_state(path, now=datetime(2026, 11, 22, 12)),
                {"sent": {}, "last_successful_reminder_scan": None},
            )
            self.assertFalse(path.exists())
