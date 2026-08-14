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


if __name__ == "__main__":
    unittest.main()
