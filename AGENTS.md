# Agent Instructions

## Scope and layers

- Read the relevant sections of [README.md](README.md) before changing parsing, Mail access, Telegram routing, operations, or deployment behavior. README is the canonical product and operations reference.
- Keep paths machine-independent. Resolve them from `__file__` or explicit environment variables; never hard-code a developer's home directory in application logic.
- Gmail full-sync and API batch sizes share `GMAIL_BATCH_SIZE` (default 10, range 1–50) and pause between sequential batches using `GMAIL_BATCH_PAUSE_SECONDS` (default 1 second). HTTP 429 and 403 rate-limit responses use up to six exponential-backoff attempts with `Retry-After` honored; 5xx/network retries retain their existing three-attempt behavior. Gmail API failure logs include only HTTP status/reason or the network exception type; never log the request URL, message ID, or response body.
- When a checkpointed Gmail cache has a missing or mismatched retention version, attempt a full-sync migration at most once per six hours. On migration failure, continue incrementally and leave the retention version unchanged.
- Deployment preflight must use `.venv/bin/python -m pip`, validate the interpreter and pip module before install, and run Ruff/Pyright only when each executable passes `--version`. Report a broken venv for manual repair; never move, delete, or recreate it automatically.
- Preserve the project layers: Mail access in `sources/`, parsing in `parsing/`, business rules and rendering in `services/`, Telegram transport in `delivery/`, and process flow in `cli.py`. Keep `main.py` as a thin compatibility entry point.
- Keep parsing independent from Telegram delivery. Parser tests must run without network access or credentials.

## Read-only and privacy rules

- Mail access is strictly read-only. Never change read status, delete, move, archive, flag, label, reply, forward, send mail, or create drafts.
- Never put tokens, credentials, private message bodies, mailbox contents, or real personal data in logs, tests, fixtures, commits, or user-facing output.
- Tests and fixtures must be synthetic and must not require a network connection or real credentials.

## Parser cache version

- Increase `mail_digest/parsing.PARSER_VERSION` whenever a change can alter parser output, so persisted parse-cache entries are invalidated.
- Keep parser cache storage and its error behavior in `services/`; consult the README's cache documentation before changing its contract.

## Verification and changes

- Add or update tests for every behavior change. Keep changes small and avoid unrelated working-tree edits.
- Run relevant tests, Python compilation, and `git diff --check` before each commit.
- Before finishing a section, activate `.venv` and run `./scripts/check.sh`; do not rely on GitHub Actions for local verification.
- Preserve the 100-message synthetic parser performance regression test and the bounded dotted-number context scan.
- Follow the README's [Test](README.md#test) instructions. Do not run dependency audits unless explicitly requested.

## Runtime ownership

- GitHub `main` is canonical. Mac Studio and MacBook Pro are development machines; Mac Mini is the production/runtime machine.
- The standalone `telegram_listener.py` is the sole Telegram polling owner. Do not start a second poller or webhook for the same bot.
- Route digest entry points through the shared `mail_digest.cli.run_digest()` service and its process-level lock; do not recreate lock-file existence checks or remove the lock file.
- Set `PYTHONUNBUFFERED=1` in all launchd plist templates so Python stdout is written to launchd logs without buffering.
- Do not load, restart, or remove launchd services, deploy, or access the Mac Mini unless the user explicitly requests that action. The deploy script's default mode is preview only; never add an implicit apply step.
- See README's [Çalıştırma modeli](README.md#çalıştırma-modeli) for operational details.
