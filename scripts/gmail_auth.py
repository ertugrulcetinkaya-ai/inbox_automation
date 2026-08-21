#!/usr/bin/env python3
"""Explicit one-time Gmail OAuth authorization command."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mail_digest.sources.gmail.interactive import authorize_interactively


def main() -> int:
    token_path = authorize_interactively()
    print(f"Gmail read-only token written securely to {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
