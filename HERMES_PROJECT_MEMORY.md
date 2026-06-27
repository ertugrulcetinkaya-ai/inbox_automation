# Project Memory: macOS Mail Unread Digest

## Overview
This is a standalone macOS Mail unread digest project. Hermes must read this file first before doing any work in this project.

## Core Functionality
|- The project reads unread emails from Apple Mail / macOS Mail.
|- It includes all configured Mail accounts.
|- It groups summaries by account/mailbox.
|- SCOPE: Only summarizes primary inbox mail. Mailboxes must be named exactly "INBOX" or "Inbox". All other folders are excluded.

## Strict Constraints (Read-Only)
- This project is strictly read-only.
- It must NEVER mark mail as read or unread.
- It must NEVER delete, move, archive, flag, reply, forward, send, label, or create drafts.

## Version 1 Specifications
- v1 does not use LLM summarization.
- v1 only extracts metadata/snippet safely:
  - account
  - mailbox
  - unread count
  - sender
  - subject
  - received date/time
  - short snippet

## Integration & Infrastructure
- Telegram sending uses: `~/.hermes_local_automation/telegram.env`
- SECURITY: Do not print or expose the Telegram bot token.
- `main.py`: Sends the digest.
- `telegram_listener.py`: Listens for `/mail`, `/unread`, and `/status`.

## Automation (launchd)
- `launchd/com.ertugrul.mail.unread.summary.plist`: Runs daily at 08:00.
- `launchd/com.ertugrul.mail.unread.listener.plist`: Starts the Telegram listener.
- `logs/`: Contains output and error logs.

## Operational Rules
- Any change to automation, launchd, Telegram, or Mail access requires explicit APPLY.
- Prefer read-only tests before installing or restarting launchd services.
