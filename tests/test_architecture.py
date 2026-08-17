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

    def test_applescript_does_not_filter_message_bodies_in_mail(self):
        script_path = Path(main.__file__).parent / "mail_fetcher.applescript"
        script = script_path.read_text(encoding="utf-8").lower()

        self.assertIn("firstnonrecent", script)
        self.assertIn("thesubject contains", script)
        self.assertIn('subject contains "meeting"', script)
        self.assertNotIn("every message of themailbox whose", script)
        self.assertNotIn("every mailbox", script)

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
