#!/usr/bin/env bash
set -euo pipefail

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

python -m compileall -q main.py telegram_listener.py mail_digest tests scripts
python -m unittest discover -s tests -p 'test_*.py'
ruff check mail_digest main.py telegram_listener.py scripts tests
pyright
git diff --check

if command -v bandit >/dev/null 2>&1; then
    bandit -q -r mail_digest main.py telegram_listener.py -ll -s B108,B608
else
    printf 'bandit is not installed; skipping optional security check.\n'
fi

if [[ "$audit" == true ]]; then
    if ! command -v pip-audit >/dev/null 2>&1; then
        printf 'pip-audit is not installed; install requirements.quality.lock to use --audit.\n' >&2
        exit 1
    fi
    pip-audit -r requirements.lock
fi
