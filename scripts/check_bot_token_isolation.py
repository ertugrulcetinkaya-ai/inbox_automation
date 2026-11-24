#!/usr/bin/env python3
"""Refuse a deploy when this listener would share a Telegram bot with another runtime.

Telegram lets only one process poll a bot. inbox_automation and
company_reporting_hub (served by Hermes) therefore use separate bots. This
check compares this project's bot token with the tokens configured for the
reporting runtime and fails if they match. Token values are never printed.
"""

from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mail_digest.config import load_env  # noqa: E402

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
OTHER_RUNTIME_ENV_FILES = (
    Path("~/.config/company_reporting_hub/telegram.env"),
    Path("~/.hermes/.env"),
)
EXTRA_FILES_VARIABLE = "OTHER_TELEGRAM_ENV_FILES"

EXIT_OK = 0
EXIT_SHARED = 1
EXIT_UNREADABLE = 2


def read_token(path: Path) -> str:
    """Return the bot token from a KEY=VALUE file, or "" when absent."""

    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, NotADirectoryError):
        return ""
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == TOKEN_KEY:
            return value.strip().strip("'\"")
    return ""


def other_runtime_files(environ=os.environ) -> list[Path]:
    files = list(OTHER_RUNTIME_ENV_FILES)
    extra = environ.get(EXTRA_FILES_VARIABLE, "")
    files.extend(Path(item) for item in extra.split(os.pathsep) if item.strip())
    return files


def own_token(environ=os.environ) -> str:
    token = environ.get(TOKEN_KEY, "")
    if token:
        return token
    return load_env().get(TOKEN_KEY, "")


def main() -> int:
    try:
        token = own_token()
    except (OSError, ValueError) as exc:
        print(f"Telegram ayar dosyası okunamadı ({exc.__class__.__name__}).", file=sys.stderr)
        return EXIT_UNREADABLE
    if not token:
        print("TELEGRAM_BOT_TOKEN tanımlı değil; listener için kendi botunuzu ayarlayın.", file=sys.stderr)
        return EXIT_UNREADABLE

    for path in other_runtime_files():
        try:
            other = read_token(path)
        except OSError as exc:
            print(f"Uyarı: {path} okunamadı ({exc.__class__.__name__}); atlandı.", file=sys.stderr)
            continue
        if other and hmac.compare_digest(other, token):
            print(
                "Bu proje ile rapor tarafı aynı Telegram botunu kullanıyor "
                f"({path} ile aynı token). İnbox için BotFather'dan ayrı bir bot "
                "oluşturup telegram.env dosyasına yazın.",
                file=sys.stderr,
            )
            return EXIT_SHARED
    print("Telegram bot ayrımı doğrulandı.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
