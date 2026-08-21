#!/usr/bin/env python3
"""Render this repository's launchd templates for the current Mac user.

The script writes plist files to ~/Library/LaunchAgents but deliberately does
not load or restart them. The Company Reporting Hermes Gateway must remain the
only Telegram polling process when the standalone listener is not requested.
"""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = PROJECT_ROOT / "launchd"
LAUNCH_AGENTS_ROOT = Path.home() / "Library" / "LaunchAgents"

TEMPLATES = {
    "summary": TEMPLATE_ROOT / "com.ertugrul.mail.unread.summary.plist.template",
    "listener": TEMPLATE_ROOT / "com.ertugrul.mail.unread.listener.plist.template",
}


def render_template(
    template_path: Path,
    mail_source="apple_mail",
    gmail_credentials_file=None,
    gmail_token_file=None,
    gmail_cache_file=None,
) -> bytes:
    gmail_root = Path.home() / ".hermes_local_automation" / "gmail"
    text = template_path.read_text(encoding="utf-8")
    rendered = text.replace("__PROJECT_ROOT__", str(PROJECT_ROOT))
    rendered = rendered.replace("__PYTHON_PATH__", str(PROJECT_ROOT / ".venv" / "bin" / "python"))
    replacements = {
        "__MAIL_SOURCE__": mail_source,
        "__GMAIL_CREDENTIALS_FILE__": str(gmail_credentials_file or gmail_root / "credentials.json"),
        "__GMAIL_TOKEN_FILE__": str(gmail_token_file or gmail_root / "token.json"),
        "__GMAIL_CACHE_FILE__": str(gmail_cache_file or gmail_root / "cache.sqlite3"),
    }
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    payload = rendered.encode("utf-8")
    plistlib.loads(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--install",
        action="store_true",
        help="Write rendered plists into ~/Library/LaunchAgents",
    )
    parser.add_argument(
        "--mail-source",
        choices=("apple_mail", "gmail"),
        default="apple_mail",
        help="Mail transport to render (default is the conservative apple_mail local setting)",
    )
    parser.add_argument("--gmail-credentials-file", type=Path)
    parser.add_argument("--gmail-token-file", type=Path)
    parser.add_argument("--gmail-cache-file", type=Path)
    parser.add_argument(
        "--include-listener",
        action="store_true",
        help="Also install the standalone Telegram listener (avoid when Hermes Gateway owns the bot)",
    )
    args = parser.parse_args()

    selected = ["summary"]
    if args.include_listener:
        selected.append("listener")

    rendered = {
        key: render_template(
            TEMPLATES[key],
            mail_source=args.mail_source,
            gmail_credentials_file=args.gmail_credentials_file,
            gmail_token_file=args.gmail_token_file,
            gmail_cache_file=args.gmail_cache_file,
        )
        for key in selected
    }
    for key in selected:
        destination = LAUNCH_AGENTS_ROOT / TEMPLATES[key].name.removesuffix(".template")
        print(f"{key}: {destination}")
        if args.install:
            LAUNCH_AGENTS_ROOT.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(rendered[key])

    if not args.install:
        print("Preview only. Re-run with --install to write the plists.")
    else:
        print("Plists installed; load them with launchctl when ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
