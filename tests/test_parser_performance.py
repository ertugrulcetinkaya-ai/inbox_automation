"""Bounded performance regression coverage for dotted numeric tokens."""

import time
import unittest

from mail_digest.config import local_now
from mail_digest.services.meeting_service import build_digest_result


class ParserPerformanceTests(unittest.TestCase):
    def test_daily_digest_handles_many_dotted_tokens_within_ten_seconds(self):
        target_date = local_now().date()
        noise = " ".join(["12.03"] * 400)
        prefix = f"Meeting scheduled {target_date.isoformat()} at 09:30."
        padding = "x" * (20_000 - len(prefix) - len(noise) - 2)
        content = f"{prefix} {padding} {noise}"
        records = [
            {
                "subject": f"Synthetic meeting {index:03d}",
                "sender": "Fixture Organizer <organizer@example.test>",
                "received_date": target_date.isoformat(),
                "source_message_id": f"synthetic-{index:03d}@example.test",
                "content": content,
            }
            for index in range(100)
        ]

        started = time.perf_counter()
        result = build_digest_result(records, "daily", target_date=target_date)
        elapsed = time.perf_counter() - started

        self.assertEqual(result.count, 100)
        self.assertEqual(len(result.meetings), 100)
        self.assertLess(elapsed, 10.0, f"daily digest took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
