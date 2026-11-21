import json
import os
import requests
import tempfile
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
from mail_digest.services.lock import DigestAlreadyRunning
from mail_digest.services.health import build_status_report
from mail_digest.services.log_rotation import LISTENER_LOG_NAMES, rotate_logs


MAX_PROCESSED_UPDATE_IDS = 4096
LISTENER_CONFLICT_BACKOFF_SECONDS = 300
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


def _run_company_report_command(text):
    from mail_digest.integrations.company_reporting import run_company_report_command

    return run_company_report_command(text)


def _run_report_session_status():
    from mail_digest.integrations.company_reporting import run_report_session_status

    return run_report_session_status()


def _run_prepare_report_session():
    from mail_digest.integrations.company_reporting import run_prepare_report_session

    return run_prepare_report_session()

def _run_digest_for_command(upcoming=False, mode=None) -> tuple[bool, str]:
    try:
        if mode is None:
            exit_code = execute_digest(upcoming=upcoming, wait_seconds=30)
        else:
            exit_code = execute_digest(mode=mode, wait_seconds=30)
    except DigestAlreadyRunning:
        return False, "Özet şu anda hazırlanıyor. Lütfen biraz sonra tekrar deneyin."
    except Exception as exc:
        return False, f"Özet çalıştırılamadı: {str(exc)[:200] or 'bilinmeyen hata'}"

    if exit_code != 0:
        return False, f"Özet çalıştırılamadı (çıkış kodu: {exit_code})."

    if mode == "weekly":
        return True, "Haftalık toplantı özeti Telegram sohbetinize gönderildi."
    if mode == "attention":
        return True, "Dikkat gerektiren toplantılar Telegram sohbetinize gönderildi."
    if upcoming:
        return True, "Bugün ve sonraki toplantı özeti Telegram sohbetinize gönderildi."
    return True, "Bugünün toplantı özeti Telegram sohbetinize gönderildi."


def run_digest(upcoming=False, mode=None):
    """Run one digest and preserve the listener's public response text."""

    _, response = _run_digest_for_command(upcoming=upcoming, mode=mode)
    return response


def _send_digest_command_response(token, chat_id, **kwargs):
    ok, response = _run_digest_for_command(**kwargs)
    if not ok:
        send_text(token, chat_id, response)


class TelegramUpdateStateError(RuntimeError):
    """The listener cannot safely load or persist its update id state."""


def _update_state_file(env):
    configured = os.environ.get("TELEGRAM_UPDATE_STATE_FILE") or env.get(
        "TELEGRAM_UPDATE_STATE_FILE"
    )
    return Path(configured or DEFAULT_UPDATE_STATE_FILE).expanduser()


def _load_processed_update_ids(state_file):
    state_file = Path(state_file).expanduser()
    if not state_file.exists():
        return set()
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
        state_file.chmod(0o600)
        return result
    except (OSError, TypeError, ValueError, KeyError, UnicodeError) as exc:
        raise TelegramUpdateStateError(
            "Telegram update state is unreadable or malformed"
        ) from exc


def _save_processed_update_ids(processed_update_ids, state_file):
    state_file = Path(state_file).expanduser()
    try:
        state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_file.parent.chmod(0o700)
    except OSError as exc:
        raise TelegramUpdateStateError("Telegram update state directory is not writable") from exc
    retained = sorted(set(processed_update_ids))[-MAX_PROCESSED_UPDATE_IDS:]
    payload = json.dumps(
        {"processed_update_ids": retained},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{state_file.name}.",
            dir=str(state_file.parent),
        )
    except OSError as exc:
        raise TelegramUpdateStateError("Telegram update state could not be staged") from exc
    descriptor_open = True
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor_open = False
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, state_file)
        state_file.chmod(0o600)
        if isinstance(processed_update_ids, set):
            processed_update_ids.clear()
            processed_update_ids.update(retained)
    except (OSError, TypeError, ValueError) as exc:
        raise TelegramUpdateStateError("Telegram update state could not be saved") from exc
    finally:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _handle_update(update, token, chat_id, allowed_user_id=None):
    if "message" not in update or not isinstance(update["message"], dict):
        return

    msg = update["message"]
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    chat_id_msg = str(chat.get("id"))
    text = msg.get("text", "")
    # Only accept commands from the configured chat. Group commands require an
    # explicit operator identity; otherwise any member could invoke commands
    # with external side effects through an accidentally shared chat.
    if chat_id_msg != str(chat_id):
        return
    chat_type = str(chat.get("type") or "").casefold()
    if chat_type in GROUP_CHAT_TYPES and not allowed_user_id:
        raise TelegramConfigurationError(
            "TELEGRAM_ALLOWED_USER_ID is required for group and supergroup chats"
        )
    if allowed_user_id is not None:
        sender = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        if str(sender.get("id")) != str(allowed_user_id):
            return

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
        send_text(token, chat_id, help_text)
        return

    if normalized.startswith(("servis", "fatura")):
        registry_result = _run_company_report_command(text)
        if isinstance(registry_result, str):
            send_text(token, chat_id, registry_result)
        return

    if normalized in {"session", "sys_session"}:
        send_text(token, chat_id, _run_report_session_status())
        return

    if normalized == "session_hazirla":
        send_text(token, chat_id, "SYS oturumu hazırlanıyor. MFA tamamlayın.")
        send_text(token, chat_id, _run_prepare_report_session())
        return

    command = (text or "").strip().lower()
    mode = MEETING_COMMANDS.get(command)
    if mode == "daily":
        print("Command received: meeting digest")
        _send_digest_command_response(token, chat_id)
    elif mode == "upcoming":
        print("Command received: upcoming meeting digest")
        _send_digest_command_response(token, chat_id, upcoming=True)
    elif mode in {"weekly", "attention"}:
        print(f"Command received: {mode} meeting digest")
        _send_digest_command_response(token, chat_id, mode=mode)
    elif command in {"/durum", "/status"}:
        print("Command received: status")
        send_text(token, chat_id, build_status_report())


def process_update(
    update,
    token,
    chat_id,
    allowed_user_id=None,
    processed_update_ids=None,
    state_file=None,
):
    """Handle one update once, persisting its id after dispatch completes."""

    if not isinstance(update, dict):
        return False
    try:
        update_id = update["update_id"]
        if isinstance(update_id, bool):
            raise ValueError
        update_id = int(update_id)
    except (KeyError, TypeError, ValueError):
        return False

    processed = processed_update_ids if processed_update_ids is not None else set()
    if update_id in processed:
        return False

    _handle_update(update, token, chat_id, allowed_user_id=allowed_user_id)
    processed.add(update_id)
    if state_file is not None:
        _save_processed_update_ids(processed, state_file)
    return True


def main():
    rotate_logs(PROJECT_ROOT / "logs", names=LISTENER_LOG_NAMES)
    print("Starting Telegram Mail Listener...")
    token = None
    try:
        env = load_env()
        token = env.get("TELEGRAM_BOT_TOKEN")
        chat_id = env.get("TELEGRAM_CHAT_ID")
        allowed_user_id = (env.get("TELEGRAM_ALLOWED_USER_ID") or "").strip() or None
        
        if not token or not chat_id:
            print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in env file.")
            return 1

        state_file = _update_state_file(env)
        try:
            processed_update_ids = _load_processed_update_ids(state_file)
        except TelegramUpdateStateError as exc:
            print(f"Fatal error in listener: {str(exc).replace(token, '[REDACTED]')}")
            return 1

        offset = 0
        while True:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"offset": offset, "timeout": 30}
            try:
                response = requests.get(url, params=params, timeout=35)
                if response.status_code == 409:
                    print(GET_UPDATES_CONFLICT_MESSAGE)
                    time.sleep(_listener_conflict_backoff_seconds())
                    return 1
                response.raise_for_status()
                data = response.json()
                
                for update in data.get("result", []):
                    if not isinstance(update, dict):
                        continue
                    try:
                        update_id = int(update["update_id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    process_update(
                        update,
                        token,
                        chat_id,
                        allowed_user_id=allowed_user_id,
                        processed_update_ids=processed_update_ids,
                        state_file=state_file,
                    )
                    # Confirm the update to Telegram only after dispatch and
                    # durable id persistence have completed.
                    offset = max(offset, update_id + 1)
            except (TelegramUpdateStateError, TelegramConfigurationError) as exc:
                safe_error = str(exc).replace(token, "[REDACTED]")
                print(f"Fatal error in listener: {safe_error}")
                return 1
            except Exception as exc:
                safe_error = str(exc).replace(token, "[REDACTED]")
                print(f"Polling error: {safe_error}")
                time.sleep(5)

    except Exception as exc:
        safe_error = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        print(f"Fatal error in listener: {safe_error}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
