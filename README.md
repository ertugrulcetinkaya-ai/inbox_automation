# Mail Unread Digest

A tool to fetch unread emails from primary inboxes via AppleScript and send a summary digest to Telegram. (Uses an allow-list for primary inbox folders).

## Installation
1. Ensure `~/.hermes_local_automation/telegram.env` contains:
   - `TELEGRAM_BOT_TOKEN=your_token`
   - `TELEGRAM_CHAT_ID=your_chat_id`

## Usage
Run the digest manually:
```bash
python3 main.py
```

## Telegram Commands
The listener allows triggering the digest via Telegram:
- `/mail` or `/unread`: Triggers the unread mail digest.
- `/status`: Checks if the listener is active.

## Deployment (launchd)
To run the listener in the background on macOS:
```bash
launchctl load ~/Projects/mail_unread_digest/launchd/com.ertugrul.mail.unread.listener.plist
```
