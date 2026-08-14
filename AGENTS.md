# Inbox Automation Project Rules

## Scope

- This repository is the `inbox_automation` project.
- Read `README.md` and `HERMES_PROJECT_MEMORY.md` before changing automation, Mail access, Telegram routing, or deployment behavior.
- Keep the project machine-independent. Resolve paths from `__file__` or explicit environment variables; do not hard-code a developer's home directory into application logic.

## Mail and privacy rules

- Read only the `ertugrul@cetinkayalar.com` account's primary Inbox.
- Inspect at most the last 30 days of messages.
- Never mark messages read or unread.
- Never delete, move, archive, flag, reply, forward, label, or create drafts.
- Never print Telegram bot tokens, credentials, full private message bodies, or other secrets in logs, tests, commits, or user-facing output.

## Meeting behavior

- Preserve the received-date context when interpreting relative phrases such as `bugün` and `yarın`.
- Classify dotted numeric tokens before extracting dates or times: `HH.MM` must not also become `DD.MM`; ambiguous `DD.MM` values require date context, while explicit-year and structurally day-first values remain dates.
- Keep daily output limited to meetings scheduled for the current day.
- Keep upcoming output inclusive of today and later parsed meetings.
- Preserve both Turkish-character and ASCII Telegram command aliases.
- Do not add LLM-dependent behavior to the v1 parser without an explicit design decision and tests.

## Runtime ownership

- GitHub `main` is the canonical source.
- Mac Studio and MacBook Pro are development machines.
- Mac Mini is the only production/runtime machine.
- When Hermes owns the Telegram bot, do not start the standalone listener; there must be only one Telegram polling/dispatch owner.
- Keep existing launchd labels stable unless a migration plan explicitly covers unload, disable, install, and verification.
- Deploy the integrated runtime through `company_reporting_hub/scripts/deploy_mac_mini.sh` after both repositories are pushed.

## Change and verification rules

- Prefer small, explicit patches and keep unrelated working-tree changes untouched.
- Add or update tests for parser, command, routing, or deployment changes.
- Run the relevant unit tests, Python compilation, and `git diff --check` before committing.
- After deployment, verify the Hermes gateway is running, both checkouts are clean and on `main`, and the installed bridge contains the current command set.
