import unittest
from pathlib import Path

import main
from mail_digest.models import Meeting
from mail_digest.parsing.meeting_parser import extract_meetings


class ArchitectureTests(unittest.TestCase):
    def test_main_remains_a_thin_compatibility_facade(self):
        main_lines = Path(main.__file__).read_text(encoding="utf-8").splitlines()

        self.assertLess(len(main_lines), 160)
        self.assertIs(main.Meeting, Meeting)
        self.assertIs(main.extract_meetings, extract_meetings)

    def _applescript_source(self):
        script_path = Path(main.__file__).parent / "mail_fetcher.applescript"
        raw = script_path.read_text(encoding="utf-8")
        return raw, raw.lower()

    def test_applescript_reads_body_for_every_message_without_subject_gate(self):
        _, script = self._applescript_source()

        # Every message in the 30-day window gets a body-read attempt.
        self.assertIn("content of themsg", script)

        # Subject retrieval is fail-open: an empty default is set before the read,
        # so a broken subject cannot drop the message.
        self.assertIn('set thesubject to ""', script)

        # The body is read after the subject is obtained...
        subject_read_idx = script.find("subject of themsg")
        content_read_idx = script.find("content of themsg")
        self.assertGreater(content_read_idx, subject_read_idx)

        # ...and there is no subject-based inclusion gate in between. A keyword
        # gate on the subject would discard body-only meetings before Python sees them.
        region = script[subject_read_idx:content_read_idx]
        self.assertNotIn("thesubject contains", region)

        # Any subject-based check (raw-source optimization only) happens after the body read.
        self.assertLess(content_read_idx, script.find("thesubject contains"))

    def test_applescript_preserves_30day_boundary_and_avoids_full_mailbox_query(self):
        _, script = self._applescript_source()

        # The 30-day boundary is located by binary search, not a full-mailbox date scan.
        self.assertIn("firstnonrecent", script)
        # No unbounded full-mailbox `whose` query that would scan every message.
        self.assertNotIn("every message of themailbox whose", script)
        self.assertNotIn("messages of themailbox whose", script)

    def test_applescript_raw_source_is_selective_and_calendar_signal_driven(self):
        _, script = self._applescript_source()

        # Raw source is still fetched conditionally, not for every message.
        self.assertIn("needsrawsource", script)
        self.assertIn("if needsrawsource is true then", script)
        self.assertIn("source of themsg", script)

        # Turkish "Davet:" subject prefix is a raw-source signal.
        self.assertIn('"davet:"', script)

        # .ics attachment metadata is a raw-source signal.
        self.assertIn("attachments of themsg", script)
        self.assertIn('".ics"', script)

        # Attachment enumeration happens only inside the raw-source decision path:
        # a needsRawSource-is-false guard precedes it.
        attach_idx = script.find("attachments of themsg")
        self.assertGreater(attach_idx, -1)
        guard_idx = script.rfind("if needsrawsource is false then", 0, attach_idx)
        self.assertGreater(guard_idx, -1)

    def test_launchd_templates_have_no_old_checkout_path(self):
        project_root = Path(main.__file__).parent
        template_paths = sorted(project_root.glob("launchd/*.plist.template"))
        self.assertTrue(template_paths)

        for template_path in template_paths:
            template = template_path.read_text(encoding="utf-8")
            self.assertIn("__PROJECT_ROOT__", template)
            self.assertIn("__PYTHON_PATH__", template)
            self.assertNotIn("/Users/ertugrulcetinkaya/Projects/mail_unread_digest", template)


if __name__ == "__main__":
    unittest.main()
