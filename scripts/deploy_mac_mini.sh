#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'Usage: %s [--apply]\n' "$0" >&2
}

APPLY=false
if [[ $# -gt 1 ]]; then
    usage
    exit 2
fi
if [[ $# -eq 1 ]]; then
    if [[ "$1" != "--apply" ]]; then
        usage
        exit 2
    fi
    APPLY=true
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

REPO_ROOT="$(git rev-parse --show-toplevel)"
if [[ "$REPO_ROOT" != "$PROJECT_ROOT" ]]; then
    printf 'Run this script from the inbox_automation repository.\n' >&2
    exit 1
fi

BRANCH="$(git branch --show-current)"
if [[ "$BRANCH" != "main" ]]; then
    printf 'Deploy requires the main branch (current: %s).\n' "$BRANCH" >&2
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    printf 'Deploy requires a clean working tree.\n' >&2
    exit 1
fi

git fetch origin
git pull --ff-only origin main

VENV_DIR="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
if [[ ! -d "$VENV_DIR" ]]; then
    PYTHON3_BIN=''
    for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
            >/dev/null 2>&1; then
            PYTHON3_BIN="$(command -v "$candidate")"
            break
        fi
    done
    if [[ -z "$PYTHON3_BIN" ]]; then
        printf 'Python 3.11 or newer is required to create .venv.\n' >&2
        exit 1
    fi
    "$PYTHON3_BIN" -m venv "$VENV_DIR"
fi

if [[ ! -x "$VENV_PYTHON" || ! -x "$VENV_PIP" ]]; then
    printf '.venv must contain executable bin/python and bin/pip.\n' >&2
    exit 1
fi

"$VENV_PIP" install -r requirements.lock

if [[ -x "$VENV_DIR/bin/ruff" && -x "$VENV_DIR/bin/pyright" ]]; then
    PATH="$VENV_DIR/bin:$PATH" PYTHON="$VENV_PYTHON" ./scripts/check.sh
else
    printf 'ruff or pyright is missing from .venv; running unittest and compileall only.\n'
    "$VENV_PYTHON" -m unittest discover -s tests -p 'test_*.py'
    "$VENV_PYTHON" -m compileall -q main.py telegram_listener.py mail_digest tests scripts
fi

if ! MAIL_SOURCE=gmail "$VENV_PYTHON" main.py --upcoming --dry-run >/dev/null 2>&1; then
    printf 'Gmail ön kontrolü başarısız; önce .venv/bin/python scripts/gmail_auth.py çalıştırın.\n' >&2
    exit 1
fi

if ! LAUNCHD_JOBS="$(launchctl list)"; then
    printf 'Unable to inspect launchd jobs; refusing deployment.\n' >&2
    exit 1
fi
if grep -qi hermes <<<"$LAUNCHD_JOBS"; then
    printf 'Hermes gateway hâlâ yüklü; önce company_reporting_hub tarafında durdurun.\n' >&2
    exit 1
fi

INSTALL_ARGS=(--mail-source gmail --include-listener)
if [[ "$APPLY" == true ]]; then
    INSTALL_ARGS+=(--install)
fi
"$VENV_PYTHON" scripts/install_launchd.py "${INSTALL_ARGS[@]}"

DOMAIN="gui/$(id -u)"
LABELS=(
    com.ertugrul.mail.unread.summary
    com.ertugrul.mail.meeting.reminder
    com.ertugrul.mail.unread.listener
)
PLISTS=(
    "$HOME/Library/LaunchAgents/com.ertugrul.mail.unread.summary.plist"
    "$HOME/Library/LaunchAgents/com.ertugrul.mail.meeting.reminder.plist"
    "$HOME/Library/LaunchAgents/com.ertugrul.mail.unread.listener.plist"
)

if [[ "$APPLY" == true ]]; then
    for label in "${LABELS[@]}"; do
        launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    done
    for plist in "${PLISTS[@]}"; do
        launchctl bootstrap "$DOMAIN" "$plist"
    done
else
    for label in "${LABELS[@]}"; do
        printf 'Would run: launchctl bootout %s/%s\n' "$DOMAIN" "$label"
    done
    for plist in "${PLISTS[@]}"; do
        printf 'Would run: launchctl bootstrap %s %s\n' "$DOMAIN" "$plist"
    done
fi

"$VENV_PYTHON" main.py --status --dry-run
