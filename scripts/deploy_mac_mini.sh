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

DEPLOY_WAIT_STEP_SECONDS="${DEPLOY_WAIT_STEP_SECONDS:-1}"
if [[ ! "$DEPLOY_WAIT_STEP_SECONDS" =~ ^[0-9]+$ ]]; then
    printf 'DEPLOY_WAIT_STEP_SECONDS must be a non-negative integer.\n' >&2
    exit 2
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

if ! "$VENV_PYTHON" -c 'import sys' >/dev/null 2>&1 \
    || ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    printf '.venv bozuk; yeniden oluşturun: mv .venv .venv.broken-%s && python3.11 -m venv .venv\n' \
        "$(date +%Y%m%d)" >&2
    exit 1
fi

"$VENV_PYTHON" -m pip install -r requirements.lock

if [[ -x "$VENV_DIR/bin/ruff" && -x "$VENV_DIR/bin/pyright" ]] \
    && "$VENV_DIR/bin/ruff" --version >/dev/null 2>&1 \
    && "$VENV_DIR/bin/pyright" --version >/dev/null 2>&1; then
    PATH="$VENV_DIR/bin:$PATH" PYTHON="$VENV_PYTHON" ./scripts/check.sh
else
    printf 'ruff or pyright is missing from .venv; running unittest and compileall only.\n'
    "$VENV_PYTHON" -m unittest discover -s tests -p 'test_*.py'
    "$VENV_PYTHON" -m compileall -q main.py telegram_listener.py mail_digest tests scripts
fi

if ! "$VENV_PYTHON" -c 'from mail_digest.config import target_email; target_email()' \
    >/dev/null 2>&1; then
    printf '~/.hermes_local_automation/mail_digest/target.env eksik\n' >&2
    exit 1
fi

PREFLIGHT_LOCK="$(mktemp "${TMPDIR:-/tmp}/inbox_preflight_lock.XXXXXX")"
PREFLIGHT_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/inbox_preflight_output.XXXXXX")"
trap 'rm -f "$PREFLIGHT_LOCK" "$PREFLIGHT_OUTPUT"' EXIT

PREFLIGHT_STATUS=0
if MAIL_SOURCE=gmail MAIL_DIGEST_LOCK_FILE="$PREFLIGHT_LOCK" \
    "$VENV_PYTHON" main.py --upcoming --dry-run >"$PREFLIGHT_OUTPUT" 2>&1; then
    :
else
    PREFLIGHT_STATUS=$?
fi
if grep -Fq 'Digest is already running' "$PREFLIGHT_OUTPUT"; then
    PREFLIGHT_STATUS=1
fi

if [[ "$PREFLIGHT_STATUS" -ne 0 ]]; then
    tail -n 20 "$PREFLIGHT_OUTPUT" >&2
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
    report_launchd_failure() {
        local failed_label="$1"
        local loaded_labels=()
        local label
        for label in "${LABELS[@]}"; do
            if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
                loaded_labels+=("$label")
            fi
        done
        local loaded_text='yok'
        if [[ ${#loaded_labels[@]} -gt 0 ]]; then
            loaded_text="${loaded_labels[*]}"
        fi
        printf 'Yüklü launchd label’ları: %s\n' "$loaded_text" >&2
        printf 'Başarısız label: %s\n' "$failed_label" >&2
    }

    wait_until_unloaded() {
        local service_label="$1"
        local attempt
        for ((attempt = 0; attempt < 30; attempt++)); do
            if ! launchctl print "$service_label" >/dev/null 2>&1; then
                return 0
            fi
            sleep "$DEPLOY_WAIT_STEP_SECONDS"
        done
        if ! launchctl print "$service_label" >/dev/null 2>&1; then
            return 0
        fi
        return 1
    }

    for index in "${!LABELS[@]}"; do
        label="${LABELS[$index]}"
        plist="${PLISTS[$index]}"
        launchctl bootout "$DOMAIN/$label" 2>/dev/null || true

        if ! wait_until_unloaded "$DOMAIN/$label"; then
            report_launchd_failure "$label"
            exit 1
        fi

        bootstrap_succeeded=false
        for bootstrap_attempt in 1 2 3; do
            # Capture stderr only; launchctl stdout is discarded.
            if bootstrap_error="$(launchctl bootstrap "$DOMAIN" "$plist" 2>&1 >/dev/null)"; then
                bootstrap_succeeded=true
                break
            fi
            printf '%s: deneme %s: %s\n' "$label" "$bootstrap_attempt" "$bootstrap_error" >&2
            if [[ "$bootstrap_attempt" -lt 3 ]]; then
                sleep "$((DEPLOY_WAIT_STEP_SECONDS * 3))"
            fi
        done
        if [[ "$bootstrap_succeeded" != true ]]; then
            report_launchd_failure "$label"
            exit 1
        fi
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
