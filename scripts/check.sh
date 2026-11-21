#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python}"
audit=false
if [[ $# -gt 1 ]]; then
    printf 'Usage: %s [--audit]\n' "$0" >&2
    exit 2
fi
if [[ $# -eq 1 ]]; then
    if [[ "$1" != "--audit" ]]; then
        printf 'Usage: %s [--audit]\n' "$0" >&2
        exit 2
    fi
    audit=true
fi

for required_tool in ruff pyright; do
    if ! command -v "$required_tool" >/dev/null 2>&1; then
        printf "%s bulunamadı; venv'i aktive edin: source .venv/bin/activate\n" \
            "$required_tool" >&2
        exit 1
    fi
done

"$PYTHON" -m compileall -q main.py telegram_listener.py mail_digest tests scripts
"$PYTHON" -m unittest discover -s tests -p 'test_*.py'
ruff check mail_digest main.py telegram_listener.py scripts tests
pyright
git diff --check

if command -v bandit >/dev/null 2>&1; then
    bandit -q -r mail_digest main.py telegram_listener.py -ll -s B108,B608
else
    printf 'bandit is not installed; skipping optional security check.\n'
fi

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck scripts/check.sh scripts/deploy_mac_mini.sh
else
    printf 'shellcheck is not installed; skipping optional shell check.\n'
fi

if [[ "$audit" == true ]]; then
    if ! command -v pip-audit >/dev/null 2>&1; then
        printf 'pip-audit is not installed; install requirements.quality.lock to use --audit.\n' >&2
        exit 1
    fi
    pip-audit -r requirements.lock
fi
