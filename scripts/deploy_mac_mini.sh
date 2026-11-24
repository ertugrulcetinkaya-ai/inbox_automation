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

PREFLIGHT_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/inbox_preflight_output.XXXXXX")"
trap 'rm -f "$PREFLIGHT_OUTPUT"' EXIT

PREFLIGHT_STATUS=0
if MAIL_SOURCE=gmail MAIL_DIGEST_PREFLIGHT_LOCK_WAIT_SECONDS="${MAIL_DIGEST_PREFLIGHT_LOCK_WAIT_SECONDS:-120}" \
    "$VENV_PYTHON" main.py --upcoming --dry-run >"$PREFLIGHT_OUTPUT" 2>&1; then
    :
else
    PREFLIGHT_STATUS=$?
fi
if [[ "$PREFLIGHT_STATUS" -eq 75 ]]; then
    printf 'Gmail ön kontrolü başarısız: üretim işi çalışıyor; birkaç dakika sonra tekrar deneyin.\n' >&2
    exit 75
fi
if [[ "$PREFLIGHT_STATUS" -ne 0 ]]; then
    printf 'Gmail ön kontrolü başarısız; önce .venv/bin/python scripts/gmail_auth.py çalıştırın.\n' >&2
    exit 1
fi

# Telegram allows a single poller per bot. Hermes keeps polling the reporting
# bot, so this listener must run on its own bot; refuse a shared token.
if ! "$VENV_PYTHON" scripts/check_bot_token_isolation.py; then
    printf 'Telegram bot ayrımı doğrulanamadı; deploy durduruldu.\n' >&2
    exit 1
fi

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

ROLLBACK_DIR=""
PREVIOUSLY_LOADED_LABELS=""
if [[ "$APPLY" == true ]]; then
    backup_timestamp="$(date +%Y%m%d_%H%M%S)"
    BACKUP_ROOT="$HOME/.hermes_local_automation/deploy-backups"
    mkdir -p "$BACKUP_ROOT"
    chmod 700 "$BACKUP_ROOT"
    ROLLBACK_DIR="$(mktemp -d "$BACKUP_ROOT/inbox_launchd_backup.${backup_timestamp}.XXXXXX")"
    chmod 700 "$ROLLBACK_DIR"
    for index in "${!LABELS[@]}"; do
        label="${LABELS[$index]}"
        plist="${PLISTS[$index]}"
        if [[ -f "$plist" ]]; then
            cp -p "$plist" "$ROLLBACK_DIR/$label.plist"
        fi
        if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
            PREVIOUSLY_LOADED_LABELS+="$label"$'\n'
        fi
    done
    printf '%s' "$PREVIOUSLY_LOADED_LABELS" >"$ROLLBACK_DIR/loaded_labels"
    chmod 600 "$ROLLBACK_DIR/loaded_labels"
    printf 'Launchd rollback backup created: %s\n' "$ROLLBACK_DIR"
fi

INSTALL_ARGS=(--mail-source gmail --include-listener)
if [[ "$APPLY" == true ]]; then
    INSTALL_ARGS+=(--install)
fi
"$VENV_PYTHON" scripts/install_launchd.py "${INSTALL_ARGS[@]}"

if [[ "$APPLY" == true ]]; then
    report_launchd_failure() {
        local failed_label="$1"
        local label
        local loaded_text='yok'
        for label in "${LABELS[@]}"; do
            if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
                if [[ "$loaded_text" == 'yok' ]]; then
                    loaded_text="$label"
                else
                    loaded_text+=" $label"
                fi
            fi
        done
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

    bootstrap_with_retries() {
        local service_label="$1"
        local plist_path="$2"
        local bootstrap_attempt
        local bootstrap_error
        local bootstrap_succeeded=false

        for bootstrap_attempt in 1 2 3; do
            if bootstrap_error="$(launchctl bootstrap "$DOMAIN" "$plist_path" 2>&1 >/dev/null)"; then
                bootstrap_succeeded=true
                break
            fi
            printf '%s: deneme %s: %s\n' "$service_label" "$bootstrap_attempt" "$bootstrap_error" >&2
            if [[ "$bootstrap_attempt" -lt 3 ]]; then
                sleep "$((DEPLOY_WAIT_STEP_SECONDS * 3))"
            fi
        done
        [[ "$bootstrap_succeeded" == true ]]
    }

    rollback_launchd() {
        local index
        local service_label
        local plist_path
        local backup_path
        local restore_ok=true

        printf 'Rollback: stopping newly loaded labels.\n' >&2
        for service_label in "${LABELS[@]}"; do
            launchctl bootout "$DOMAIN/$service_label" >/dev/null 2>&1 || true
            if wait_until_unloaded "$DOMAIN/$service_label"; then
                printf 'Rollback: unloaded %s.\n' "$service_label" >&2
            else
                printf 'Rollback: unload wait failed for %s.\n' "$service_label" >&2
                restore_ok=false
            fi
        done

        for index in "${!LABELS[@]}"; do
            service_label="${LABELS[$index]}"
            plist_path="${PLISTS[$index]}"
            backup_path="$ROLLBACK_DIR/$service_label.plist"
            if [[ -f "$backup_path" ]]; then
                if cp -p "$backup_path" "$plist_path"; then
                    printf 'Rollback: restored plist for %s.\n' "$service_label" >&2
                else
                    printf 'Rollback: plist restore failed for %s.\n' "$service_label" >&2
                    restore_ok=false
                fi
            elif rm -f "$plist_path"; then
                printf 'Rollback: removed new plist for %s.\n' "$service_label" >&2
            else
                printf 'Rollback: plist removal failed for %s.\n' "$service_label" >&2
                restore_ok=false
            fi
        done

        while IFS= read -r service_label; do
            [[ -n "$service_label" ]] || continue
            for index in "${!LABELS[@]}"; do
                if [[ "${LABELS[$index]}" == "$service_label" ]]; then
                    plist_path="${PLISTS[$index]}"
                    break
                fi
            done
            if [[ ! -f "$plist_path" ]]; then
                printf 'Rollback: previous plist missing for %s.\n' "$service_label" >&2
                restore_ok=false
                continue
            fi
            if ! wait_until_unloaded "$DOMAIN/$service_label"; then
                printf 'Rollback: unload wait failed before bootstrap for %s.\n' "$service_label" >&2
                restore_ok=false
                continue
            fi
            if bootstrap_with_retries "$service_label" "$plist_path"; then
                printf 'Rollback: bootstrapped previous service %s.\n' "$service_label" >&2
            else
                printf 'Rollback: bootstrap failed for previous service %s.\n' "$service_label" >&2
                restore_ok=false
            fi
        done <<<"$PREVIOUSLY_LOADED_LABELS"

        if [[ "$restore_ok" == true ]]; then
            printf 'Rollback completed; previous loaded services were restored.\n' >&2
        else
            printf 'Rollback completed with errors; manual launchd recovery may be required.\n' >&2
        fi
    }

    for index in "${!LABELS[@]}"; do
        label="${LABELS[$index]}"
        plist="${PLISTS[$index]}"
        launchctl bootout "$DOMAIN/$label" 2>/dev/null || true

        if ! wait_until_unloaded "$DOMAIN/$label"; then
            report_launchd_failure "$label"
            rollback_launchd
            exit 1
        fi

        if ! bootstrap_with_retries "$label" "$plist"; then
            report_launchd_failure "$label"
            rollback_launchd
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
