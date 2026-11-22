import json
import os
import requests
import time
import warnings
from pathlib import Path

# Suppress urllib3 NotOpenSSLWarning
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

from mail_digest.cli import run_digest as execute_digest
from mail_digest.commands import MEETING_COMMANDS, meeting_help_text
from mail_digest.config import PROJECT_ROOT, load_env
from mail_digest.delivery.telegram import send_text
from mail_digest.fileio import atomic_write_private
from mail_digest.integrations.company_reporting import REPORT_SERVICE_UNAVAILABLE
from mail_digest.services.lock import DigestAlreadyRunning
from mail_digest.services.health import build_status_report
from mail_digest.services.log_rotation import LISTENER_LOG_NAMES, rotate_logs


MAX_PROCESSED_UPDATE_IDS = 4096
MAX_UPDATE_ATTEMPTS = 3
LISTENER_CONFLICT_BACKOFF_SECONDS = 300
POLL_BACKOFF_INITIAL_SECONDS = 5
POLL_BACKOFF_MAX_SECONDS = 60
LISTENER_LOG_ROTATION_INTERVAL_SECONDS = 60 * 60
DONE = "DONE"
RETRY = "RETRY"
FAILED = "FAILED"
GET_UPDATES_CONFLICT_MESSAGE = (
    "Başka bir Telegram poller/webhook bu botu kullanıyor; listener durduruldu."
)
DEFAULT_UPDATE_STATE_FILE = (
    Path.home() / ".hermes_local_automation" / "telegram" / "update_state.json"
)
GROUP_CHAT_TYPES = {"group", "supergroup"}


class TelegramConfigurationError(RuntimeError):
    """The listener configuration is unsafe for the addressed chat type."""


def _listener_conflict_backoff_seconds():
    configured = os.environ.get("LISTENER_CONFLICT_BACKOFF_SECONDS")
    if configured is None:
        return LISTENER_CONFLICT_BACKOFF_SECONDS
    try:
        return max(0, int(configured))
    except ValueError:
        return LISTENER_CONFLICT_BACKOFF_SECONDS


def _rotate_listener_logs_if_due(last_rotation_at, now=None):
    current = time.monotonic() if now is None else now
    if current - last_rotation_at < LISTENER_LOG_ROTATION_INTERVAL_SECONDS:
        return last_rotation_at
    rotate_logs(PROJECT_ROOT / "logs", names=LISTENER_LOG_NAMES)
    return current


def _run_company_report_command(text):
    from mail_digest.integrations.company_reporting import run_company_report_command

    return run_company_report_command(text)


def _run_report_session_status():
    from mail_digest.integrations.company_reporting import run_report_session_status

    return run_report_session_status()


def _run_prepare_report_session():
    from mail_digest.integrations.company_reporting import run_prepare_report_session

    return run_prepare_report_session()

def _run_digest_for_command(upcoming=False, mode=None) -> tuple[str, str]:
    try:
        if mode is None:
            exit_code = execute_digest(upcoming=upcoming, wait_seconds=30)
        else:
            exit_code = execute_digest(mode=mode, wait_seconds=30)
    except DigestAlreadyRunning:
        return RETRY, "Özet şu anda hazırlanıyor. Lütfen biraz sonra tekrar deneyin."
    except Exception as exc:
        print(f"Digest command failed ({exc.__class__.__name__})", flush=True)
        return FAILED, "Özet tamamlanamadı; yinelenmemesi için bu komut tekrar çalıştırılmayacak."

    if exit_code != 0:
        return FAILED, "Özet tamamlanamadı; yinelenmemesi için bu komut tekrar çalıştırılmayacak."

    if mode == "weekly":
        return DONE, "Haftalık toplantı özeti Telegram sohbetinize gönderildi."
    if mode == "attention":
        return DONE, "Dikkat gerektiren toplantılar Telegram sohbetinize gönderildi."
    if upcoming:
        return DONE, "Bugün ve sonraki toplantı özeti Telegram sohbetinize gönderildi."
    return DONE, "Bugünün toplantı özeti Telegram sohbetinize gönderildi."


def run_digest(upcoming=False, mode=None):
    """Run one digest and preserve the listener's public response text."""

    _, response = _run_digest_for_command(upcoming=upcoming, mode=mode)
    return response


def _send_digest_command_response(token, chat_id, **kwargs):
    outcome, response = _run_digest_for_command(**kwargs)
    if outcome == DONE:
        return DONE
    if outcome == FAILED:
        _send_side_effect_error(token, chat_id, response)
        return FAILED
    return DONE if send_text(token, chat_id, response) else RETRY


def _send_side_effect_error(token, chat_id, message):
    """Try to report a terminal side-effect failure without retrying it."""

    try:
        send_text(token, chat_id, message)
    except Exception as exc:
        print(f"Side-effect error response failed ({exc.__class__.__name__})", flush=True)


class TelegramUpdateStateError(RuntimeError):
    """The listener cannot safely load or persist its update id state."""


def _update_state_file(env):
    configured = os.environ.get("TELEGRAM_UPDATE_STATE_FILE") or env.get(
        "TELEGRAM_UPDATE_STATE_FILE"
    )
    return Path(configured or DEFAULT_UPDATE_STATE_FILE).expanduser()


def _load_listener_state(state_file):
    state_file = Path(state_file).expanduser()
    if not state_file.exists():
        return set(), {}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        values = payload["processed_update_ids"]
        if not isinstance(values, list):
            raise ValueError("processed_update_ids must be a list")
        result = set()
        for value in values:
            if isinstance(value, bool):
                raise ValueError("update id must be an integer")
            result.add(int(value))
        raw_attempts = payload.get("update_attempts", {})
        if not isinstance(raw_attempts, dict):
            raise ValueError("update_attempts must be an object")
        attempts = {}
        for raw_update_id, raw_count in raw_attempts.items():
            if isinstance(raw_count, bool):
                raise ValueError("update attempt count must be an integer")
            update_id = int(raw_update_id)
            count = int(raw_count)
            if count < 1 or count >= MAX_UPDATE_ATTEMPTS or update_id in result:
                raise ValueError("update attempt state is invalid")
            attempts[update_id] = count
        state_file.chmod(0o600)
        return result, attempts
    except (OSError, TypeError, ValueError, KeyError, UnicodeError) as exc:
        raise TelegramUpdateStateError(
            "Telegram update state is unreadable or malformed"
        ) from exc


def _load_processed_update_ids(state_file):
    return _load_listener_state(state_file)[0]


def _save_processed_update_ids(processed_update_ids, state_file, update_attempts=None):
    state_file = Path(state_file).expanduser()
    retained = sorted(set(processed_update_ids))[-MAX_PROCESSED_UPDATE_IDS:]
    attempts = dict(update_attempts or {})
    attempts = {
        int(update_id): int(count)
        for update_id, count in attempts.items()
        if int(count) > 0 and int(update_id) not in retained
    }
    retained_attempts = dict(sorted(attempts.items())[-MAX_PROCESSED_UPDATE_IDS:])
    payload = json.dumps(
        {
            "processed_update_ids": retained,
            "update_attempts": {
                str(update_id): count
                for update_id, count in retained_attempts.items()
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    try:
        atomic_write_private(state_file, payload + "\n")
        if isinstance(processed_update_ids, set):
            processed_update_ids.clear()
            processed_update_ids.update(retained)
        if isinstance(update_attempts, dict):
            update_attempts.clear()
            update_attempts.update(retained_attempts)
    except (OSError, TypeError, ValueError) as exc:
        raise TelegramUpdateStateError("Telegram update state could not be saved") from exc


def _handle_update(update, token, chat_id, allowed_user_id=None):
    if "message" not in update or not isinstance(update["message"], dict):
        return DONE

    msg = update["message"]
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    chat_id_msg = str(chat.get("id"))
    text = msg.get("text", "")
    # Only accept commands from the configured chat. Group commands require an
    # explicit operator identity; otherwise any member could invoke commands
    # with external side effects through an accidentally shared chat.
    if chat_id_msg != str(chat_id):
        return DONE
    chat_type = str(chat.get("type") or "").casefold()
    if chat_type in GROUP_CHAT_TYPES and not allowed_user_id:
        raise TelegramConfigurationError(
            "TELEGRAM_ALLOWED_USER_ID is required for group and supergroup chats"
        )
    if allowed_user_id is not None:
        sender = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        if str(sender.get("id")) != str(allowed_user_id):
            return DONE

    normalized = (text or "").strip().lower().lstrip("/")
    if normalized in {"komutlar", "help"}:
        reporting_result = _run_company_report_command(text)
        if isinstance(reporting_result, dict):
            reporting_help = reporting_result.get("response")
        else:
            reporting_help = reporting_result
        help_text = meeting_help_text()
        if reporting_help:
            help_text = f"{help_text}\n\n{reporting_help}"
        return DONE if send_text(token, chat_id, help_text) else RETRY

    if normalized.startswith(("servis", "fatura")):
        try:
            registry_result = _run_company_report_command(text)
        except Exception as exc:
            print(f"Company report command failed ({exc.__class__.__name__})", flush=True)
            _send_side_effect_error(
                token,
                chat_id,
                "Rapor komutu tamamlanamadı; yinelenmemesi için tekrar çalıştırılmayacak.",
            )
            return FAILED
        if isinstance(registry_result, str):
            if registry_result == REPORT_SERVICE_UNAVAILABLE:
                return DONE if send_text(token, chat_id, registry_result) else RETRY
            try:
                delivered = send_text(token, chat_id, registry_result)
            except Exception as exc:
                print(f"Company report response failed ({exc.__class__.__name__})", flush=True)
                return FAILED
            return DONE if delivered else FAILED
        if isinstance(registry_result, dict) and registry_result.get("ok") is False:
            return FAILED
        return DONE

    if normalized in {"session", "sys_session"}:
        return DONE if send_text(token, chat_id, _run_report_session_status()) else RETRY

    if normalized == "session_hazirla":
        if not send_text(token, chat_id, "SYS oturumu hazırlanıyor. MFA tamamlayın."):
            return RETRY
        try:
            response = _run_prepare_report_session()
        except Exception as exc:
            print(f"Session preparation failed ({exc.__class__.__name__})", flush=True)
            _send_side_effect_error(
                token,
                chat_id,
                "Oturum hazırlığı tamamlanamadı; yinelenmemesi için tekrar çalıştırılmayacak.",
            )
            return FAILED
        try:
            delivered = send_text(token, chat_id, response)
        except Exception as exc:
            print(f"Session preparation response failed ({exc.__class__.__name__})", flush=True)
            return FAILED
        return DONE if delivered else FAILED

    command = (text or "").strip().lower()
    mode = MEETING_COMMANDS.get(command)
    if mode == "daily":
        print("Command received: meeting digest", flush=True)
        return _send_digest_command_response(token, chat_id)
    elif mode == "upcoming":
        print("Command received: upcoming meeting digest", flush=True)
        return _send_digest_command_response(token, chat_id, upcoming=True)
    elif mode in {"weekly", "attention"}:
        print(f"Command received: {mode} meeting digest", flush=True)
        return _send_digest_command_response(token, chat_id, mode=mode)
    elif command in {"/durum", "/status"}:
        print("Command received: status", flush=True)
        return DONE if send_text(token, chat_id, build_status_report()) else RETRY
    return DONE


def process_update(
    update,
    token,
    chat_id,
    allowed_user_id=None,
    processed_update_ids=None,
    state_file=None,
    update_attempts=None,
):
    """Handle one update and persist its retry or terminal state."""

    if not isinstance(update, dict):
        return FAILED
    try:
        update_id = update["update_id"]
        if isinstance(update_id, bool):
            raise ValueError
        update_id = int(update_id)
    except (KeyError, TypeError, ValueError):
        return FAILED

    processed = processed_update_ids if processed_update_ids is not None else set()
    if update_id in processed:
        return DONE

    attempts = update_attempts if update_attempts is not None else {}
    error_type = "DeliveryFailure"
    try:
        outcome = _handle_update(update, token, chat_id, allowed_user_id=allowed_user_id)
    except TelegramConfigurationError:
        raise
    except Exception as exc:
        outcome = RETRY
        error_type = exc.__class__.__name__

    if outcome not in {DONE, RETRY, FAILED}:
        outcome = RETRY
        error_type = "InvalidUpdateResult"

    if outcome in {DONE, FAILED}:
        processed.add(update_id)
        attempts.pop(update_id, None)
    else:
        attempt_count = attempts.get(update_id, 0) + 1
        if attempt_count >= MAX_UPDATE_ATTEMPTS:
            processed.add(update_id)
            attempts.pop(update_id, None)
            outcome = FAILED
            print(
                f"Update failed after {MAX_UPDATE_ATTEMPTS} attempts ({error_type})",
                flush=True,
            )
        else:
            attempts[update_id] = attempt_count

    if state_file is not None:
        _save_processed_update_ids(processed, state_file, attempts)
    return outcome


def main():
    rotate_logs(PROJECT_ROOT / "logs", names=LISTENER_LOG_NAMES)
    last_log_rotation_at = time.monotonic()
    print("Starting Telegram Mail Listener...", flush=True)
    token = None
    try:
        env = load_env()
        token = env.get("TELEGRAM_BOT_TOKEN")
        chat_id = env.get("TELEGRAM_CHAT_ID")
        allowed_user_id = (env.get("TELEGRAM_ALLOWED_USER_ID") or "").strip() or None
        
        if not token or not chat_id:
            print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in env file.", flush=True)
            return 1

        state_file = _update_state_file(env)
        try:
            processed_update_ids, update_attempts = _load_listener_state(state_file)
        except TelegramUpdateStateError as exc:
            print(
                f"Fatal error in listener ({exc.__class__.__name__})",
                flush=True,
            )
            return 1

        offset = 0
        poll_backoff = POLL_BACKOFF_INITIAL_SECONDS
        while True:
            last_log_rotation_at = _rotate_listener_logs_if_due(last_log_rotation_at)
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"offset": offset, "timeout": 30}
            try:
                response = requests.get(url, params=params, timeout=35)
                if response.status_code == 409:
                    print(GET_UPDATES_CONFLICT_MESSAGE, flush=True)
                    time.sleep(_listener_conflict_backoff_seconds())
                    return 1
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("result", []), list):
                    raise ValueError("invalid Telegram poll response")
                poll_backoff = POLL_BACKOFF_INITIAL_SECONDS
                
                for update in data.get("result", []):
                    if not isinstance(update, dict):
                        continue
                    try:
                        update_id = int(update["update_id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    outcome = process_update(
                        update,
                        token,
                        chat_id,
                        allowed_user_id=allowed_user_id,
                        processed_update_ids=processed_update_ids,
                        state_file=state_file,
                        update_attempts=update_attempts,
                    )
                    if outcome == RETRY:
                        break
                    # Confirm the update to Telegram only after dispatch and
                    # durable id persistence have completed.
                    offset = max(offset, update_id + 1)
            except (TelegramUpdateStateError, TelegramConfigurationError) as exc:
                print(f"Fatal error in listener ({exc.__class__.__name__})", flush=True)
                return 1
            except Exception as exc:
                print(
                    f"Polling error ({exc.__class__.__name__}); retrying in {poll_backoff} seconds",
                    flush=True,
                )
                time.sleep(poll_backoff)
                poll_backoff = min(POLL_BACKOFF_MAX_SECONDS, poll_backoff * 2)

    except Exception as exc:
        print(f"Fatal error in listener ({exc.__class__.__name__})", flush=True)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
