"""Keep parser-cache tests isolated from a developer's home directory."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


_CACHE_DIRECTORY = tempfile.TemporaryDirectory(prefix="inbox-automation-tests-")
TEST_PARSE_CACHE_FILE = Path(_CACHE_DIRECTORY.name) / "parse-cache.sqlite3"


def isolate_parse_cache():
    os.environ["MAIL_DIGEST_PARSE_CACHE_FILE"] = str(TEST_PARSE_CACHE_FILE)
