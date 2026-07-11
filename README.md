# macOS Mail Unread Digest

A lightweight automation to fetch unread emails from Apple Mail and send a summary digest to Telegram.

## Architecture
- **AppleScript (`mail_fetcher.applescript`)**: Extracts metadata (account, mailbox, sender, subject, date, snippet) for unread messages in the primary Inbox. It outputs raw delimited records to avoid JSON encoding failures caused by control characters or invisible Unicode in email content.
- **Python (`main.py`)**: 
  - Executes the AppleScript.
  - Parses the delimited records.
  - Sanitizes all fields (removes control chars, zero-width spaces, collapses whitespace).
  - Groups messages by account and mailbox.
  - Formats the final digest text (v1: shows sender, date, and subject; excludes snippets for clarity).
  - Sends the digest via Telegram Bot API.

## Setup
1. Create the environment file at `~/.hermes_local_automation/telegram.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```
2. Ensure Apple Mail is open and has granted permissions to the terminal/script.

## Usage
- **Run Digest**: `python3 main.py`
- **Dry Run (Test)**: `python3 main.py --dry-run` (Fetches and prints to stdout without sending)
- **Listener**: `python3 telegram_listener.py` (Responds to `/mail`, `/unread`, `/status`)

## Permanent Fix for JSON Failures
Previously, the AppleScript attempted to generate JSON strings manually. This was fragile when emails contained quotes, backslashes, or invisible control characters. The current version uses a safe delimiter (`__MAIL_DIGEST_FIELD__`) and delegates all sanitization and JSON handling (if any) to Python's robust standard library.
