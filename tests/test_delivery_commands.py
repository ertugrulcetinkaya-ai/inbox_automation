from __future__ import annotations

import threading
import unittest

from mail_digest.delivery.commands import (
    DUPLICATE,
    QUEUED,
    CommandWorker,
    parse_command,
)


class CommandParsingTests(unittest.TestCase):
    def test_parser_separates_arguments_and_removes_bot_suffix(self):
        self.assertEqual(
            parse_command(" /HaFtA@ExampleBot gelecek 7 gün "),
            parse_command("/hafta gelecek 7 gün"),
        )
        parsed = parse_command(" /HaFtA@ExampleBot gelecek 7 gün ")
        self.assertEqual(parsed.name, "/hafta")
        self.assertEqual(parsed.args, "gelecek 7 gün")

    def test_plain_text_is_not_a_command(self):
        self.assertIsNone(parse_command("servis"))
        self.assertIsNone(parse_command("  "))


class CommandWorkerTests(unittest.TestCase):
    def test_duplicate_pending_command_is_rejected_and_job_waits_for_persistence(self):
        worker = CommandWorker()
        worker.start()
        started = threading.Event()

        def callback():
            started.set()

        try:
            status, job = worker.enqueue(("digest", "daily"), callback)
            duplicate_status, duplicate_job = worker.enqueue(
                ("digest", "daily"), callback
            )

            self.assertEqual(status, QUEUED)
            self.assertEqual(duplicate_status, DUPLICATE)
            self.assertIsNone(duplicate_job)
            self.assertFalse(started.wait(0.05))

            worker.activate(job, persisted=True)
            self.assertTrue(started.wait(1))
        finally:
            if "job" in locals() and not job.ready.is_set():
                worker.activate(job, persisted=False)
            worker.close(timeout=1)


if __name__ == "__main__":
    unittest.main()
