# Project Memory: macOS Mail Meeting Digest

## Overview
This is a standalone macOS Mail meeting digest project. Hermes must read this file first before doing any work in this project.

## Core Functionality
- The project reads recent messages from Apple Mail / macOS Mail.
- SCOPE: Only the account configured with `ertugrul@cetinkayalar.com` is included.
- SCOPE: Only the primary inbox is included. Mailboxes must be named exactly "INBOX" or "Inbox". All other folders are excluded.
- Messages received in the last 30 days are inspected; read status does not matter.
- Likely meeting/calendar messages are parsed for Turkish and English date/time formats.
- The daily output contains meetings scheduled for the current day. If none are found, it sends "Bugün toplantı yok."

## Strict Constraints (Read-Only)
- This project is strictly read-only.
- It must NEVER mark mail as read or unread.
- It must NEVER delete, move, archive, flag, reply, forward, send, label, or create drafts.

## Version 1 Specifications
|- v1 does not use LLM summarization.
|- v1 extracts meeting data safely:
|  - sender
|  - subject
|  - meeting date
|  - meeting start/end time when available

## Permanent Fix for Control Characters (June 2026)
- AppleScript no longer emits JSON. It outputs line-based records using a safe delimiter (`__MAIL_DIGEST_FIELD__`).
- Python (`main.py`) owns all sanitization, parsing, and formatting.
- Sanitization includes removing ASCII control characters, zero-width spaces, and collapsing whitespace to prevent JSON/Telegram failures.

## Integration & Infrastructure
- Telegram sending uses: `~/.hermes_local_automation/telegram.env`
- SECURITY: Do not print or expose the Telegram bot token.
- `main.py`: Sends the daily meeting digest.
- `telegram_listener.py`: Standalone listener for `/toplantilar`, `/bugun`, and `/durum`; the old English commands remain aliases. Do not run it when Company Reporting/Hermes owns the same Telegram bot.

## Automation (launchd)
- `launchd/*.plist.template`: Machine-independent launchd templates.
- `scripts/install_launchd.py`: Renders templates using the current checkout and user paths.
- The summary agent runs daily at 08:00. The standalone listener is optional and must not share a Telegram bot with Hermes Gateway.
- `logs/`: Contains output and error logs.

## Operational Rules
- Any change to automation, launchd, Telegram, or Mail access requires explicit APPLY.
- Prefer read-only tests before installing or restarting launchd services.
