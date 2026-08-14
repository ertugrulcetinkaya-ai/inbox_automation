# Project Memory: Inbox Automation

## Overview
This is a standalone inbox automation project backed by macOS Mail. Hermes must read this file first before doing any work in this project.

## Core Functionality
- The project reads recent messages from Apple Mail / macOS Mail.
- SCOPE: Only the account configured with `ertugrul@cetinkayalar.com` is included.
- SCOPE: Only the primary inbox is included. Mailboxes must be named exactly "INBOX" or "Inbox". All other folders are excluded.
- Messages received in the last 30 days are inspected; read status does not matter.
- Likely meeting/calendar messages are parsed for Turkish and English date/time formats.
- ICS-first parsing is mandatory: parse inline `VCALENDAR/VEVENT` and MIME `text/calendar`/`.ics` parts before semantic text fallback.
- The canonical `Meeting` fields are `uid`, `title`, `organizer`, `start_at`, `end_at`, `timezone`, `location`, `join_url`, `status`, `source_message_id`, and `confidence`.
- Meeting status is normalized to `CONFIRMED`, `CANCELLED`, `RESCHEDULED`, or `TENTATIVE`; `STATUS:CANCELLED` and `METHOD:CANCEL` must suppress the event.
- For the same ICS `UID`, the highest `SEQUENCE` wins. Semantic reschedule messages must keep the new date and discard the old date.
- AppleScript transport must preserve content line breaks and may carry raw MIME source; do not flatten ICS content before Python parsing.
- Dotted numeric tokens are classified before extraction: `10.30` and `10.30–11.30` remain times, while `15.08`/`15.08.2026` remain dates; ambiguous `DD.MM` tokens require nearby date context.
- Weekday expressions are anchored to the received date: bare `Cuma` resolves to the next occurrence, `bu/this` keeps the next practical occurrence, and `önümüzdeki/next` requires a strictly future occurrence when the message arrives on that weekday.
- Yearless dates use the target year first; when that candidate is at least 60 days in the past, the next-year candidate is evaluated. For example, `5 Ocak` on 20 December resolves to 5 January of the following year, while a recent past date stays in the current year.
- Relative phrases such as "bugün" and "yarın" are anchored to the message received date.
- The daily output contains meetings scheduled for the current day. If none are found, it sends "Bugün toplantı yok."
- `main.py` is a thin backward-compatible facade. The implementation is split under `mail_digest/`: `sources/apple_mail.py`, `parsing/`, `services/meeting_service.py`, `delivery/telegram.py`, and `cli.py`.
- Parsing layers must not import Telegram delivery or require network credentials; this keeps date/ICS changes independently testable.

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
- Python (`mail_digest/`) owns all sanitization, parsing, and formatting; `main.py` remains the compatibility entry point.
- Sanitization includes removing ASCII control characters, zero-width spaces, and collapsing whitespace to prevent JSON/Telegram failures.

## Integration & Infrastructure
- Telegram sending uses: `~/.hermes_local_automation/telegram.env`
- SECURITY: Do not print or expose the Telegram bot token.
- `mail_digest/cli.py`: Sends the daily meeting digest; `main.py` delegates to it for launchd compatibility.
- `telegram_listener.py`: Standalone listener for `/toplantilar`/`/toplantılar`, `/bugun`/`/bugün`, `/gelecek_toplantilar`/`/gelecek_toplantılar`, `/toplantilar_gelecek`/`/toplantılar_gelecek`, `/sonraki_toplantilar`/`/sonraki_toplantılar`, and `/durum`; ASCII aliases remain supported. Do not run it when Company Reporting/Hermes owns the same Telegram bot.

## Automation (launchd)
- `launchd/*.plist.template`: Machine-independent launchd templates.
- `scripts/install_launchd.py`: Renders templates using the current checkout and user paths.
- The summary agent runs daily at 08:00. The standalone listener is optional and must not share a Telegram bot with Hermes Gateway.
- `logs/`: Contains output and error logs.

## Operational Rules
- Any change to automation, launchd, Telegram, or Mail access requires explicit APPLY.
- Prefer read-only tests before installing or restarting launchd services.
