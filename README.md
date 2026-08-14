# macOS Mail Meeting Digest

A lightweight macOS automation that scans recent messages for `ertugrul@cetinkayalar.com`, finds meetings scheduled for the current day, and sends the daily list to Telegram.

## Architecture
- **AppleScript (`mail_fetcher.applescript`)**: Reads only the target account's primary Inbox, includes read and unread messages received in the last 30 days, and loads full content only for likely meeting/calendar messages.
- **Python (`main.py`)**:
  - Executes the AppleScript and parses the delimited records.
  - Sanitizes all fields (removes control chars, zero-width spaces, and line breaks).
  - Detects Turkish and English meeting signals and date/time formats.
  - Resolves relative words such as "bugün" against the message's received date.
  - Lists meetings whose date is today, or sends `Bugün toplantı yok.` when there are none.
  - Sends the digest via Telegram Bot API.

## Setup
1. Create the environment file at `~/.hermes_local_automation/telegram.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```
2. Ensure Apple Mail is open and has granted permissions to the terminal/script.
3. Install dependencies in the repository-local virtual environment:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

## Usage
- **Run Digest**: `python3 main.py`
- **Dry Run (Test)**: `python3 main.py --dry-run` (Fetches and prints to stdout without sending)
- **Upcoming Meetings**: `python3 main.py --upcoming` (Lists today and later meetings)
- **Listener**: `python3 telegram_listener.py` (Responds to `/toplantilar` or `/toplantılar`, `/bugun` or `/bugün`, `/gelecek_toplantilar` or `/gelecek_toplantılar`, `/toplantilar_gelecek` or `/toplantılar_gelecek`, `/sonraki_toplantilar` or `/sonraki_toplantılar`, and `/durum`)

The scheduled 08:00 launchd job calls the same `main.py` flow. Render the launchd plist for the current machine with:

```bash
.venv/bin/python scripts/install_launchd.py --install
```

Then load only the summary agent when Company Reporting/Hermes owns the Telegram bot. Do not install the standalone listener in that setup; it would create a second Telegram polling process. The listener is available only for standalone deployments with `--include-listener`.

The code derives its project path from `__file__`. `TELEGRAM_ENV_FILE` can override the default credentials path, and `COMPANY_REPORT_ROOT` can point to a differently located Company Reporting checkout.

## Permanent Fix for JSON Failures
Previously, the AppleScript attempted to generate JSON strings manually. This was fragile when emails contained quotes, backslashes, or invisible control characters. The current version uses a safe delimiter (`__MAIL_DIGEST_FIELD__`) and delegates all sanitization and JSON handling (if any) to Python's robust standard library.
