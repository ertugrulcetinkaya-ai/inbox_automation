"""Keep parser output changes paired with an explicit cache-version bump."""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date, datetime, timedelta

from fixture_helpers import load_fixture
from mail_digest.parsing import PARSER_VERSION
from mail_digest.parsing.meeting_parser import extract_meetings


CANONICAL_TODAY = date(2026, 8, 12)
CANONICAL_FIXTURES = (
    "dotted_time_10_30.txt",
    "multiple_dates.txt",
    "reschedule_tr.txt",
    "cancelled_tr.txt",
    "ics_duplicate.ics",
)
PARSER_OUTPUT_HASHES = {
    5: "b09d56d161893dbc621248dfcf878d9015b0f1bb4b59f3fcf06410b991e8ce38",
}


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported canonical parser value: {type(value).__name__}")


def _canonical_parser_output():
    rows = []
    end_date = CANONICAL_TODAY + timedelta(days=30)
    for fixture_name in CANONICAL_FIXTURES:
        rows.append(
            {
                "fixture": fixture_name,
                "meetings": extract_meetings(
                    load_fixture(fixture_name),
                    CANONICAL_TODAY,
                    end_date,
                    include_cancelled=True,
                ),
            }
        )
    return json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


class ParserVersionGuardTests(unittest.TestCase):
    def test_canonical_output_hash_is_registered_for_current_parser_version(self):
        expected_hash = PARSER_OUTPUT_HASHES.get(PARSER_VERSION)
        self.assertIsNotNone(
            expected_hash,
            "Parser çıktısı değişti; PARSER_VERSION'ı artırın ve hash'i güncelleyin.",
        )
        actual_hash = hashlib.sha256(
            _canonical_parser_output().encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            actual_hash,
            expected_hash,
            "Parser çıktısı değişti; PARSER_VERSION'ı artırın ve hash'i güncelleyin.",
        )


if __name__ == "__main__":
    unittest.main()
