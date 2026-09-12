import json
import os
import random
import requests
import tempfile
import time
import warnings
import asyncio
from pathlib import Path

# Suppress urllib3 NotOpenSSLWarning
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

from mail_digest.cli import run_digest as execute_digest
from mail_digest.config import load_env
from mail_digest.services.lock import DigestAlreadyRunning


TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
TELEGRAM_MAX_ATTEMPTS = 3
MAX_PROCESSED_UPDATE_IDS = 4096
DEFAULT_UPDATE_STATE_FILE = (
    Path.home() / ".hermes_local_automation" / "telegram" / "update_state.json"
)
GROUP_CHAT_TYPES = {"group", "supergroup"}


class TelegramConfigurationError(RuntimeError):
    """The listener configuration is unsafe for the addressed chat type."""


def _retry_delay_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    if delay < 0:
        return None
    return min(30.0, delay)


def _retry_after(response):
    payload = None
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            payload = json_method()
        except (AttributeError, TypeError, ValueError):
            payload = None
    if isinstance(payload, dict):
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            delay = _retry_delay_value(parameters.get("retry_after"))
            if delay is not None:
                return delay

    headers = getattr(response, "headers", {}) or {}
    value = None
    if hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
    return _retry_delay_value(value)


def _listener_retry_delay(response, attempt):
    if getattr(response, "status_code", None) == 429:
        retry_after = _retry_after(response)
        if retry_after is not None:
            return retry_after
    return min(30.0, 0.5 * (2 ** (attempt - 1)) + random.uniform(0, 0.1))


def send_message(token, chat_id, text, sleep=None):
    sleep = sleep or time.sleep
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, timeout=10)
        except Exception as exc:
            retryable = isinstance(exc, (requests.RequestException, OSError, TimeoutError))
            if retryable and attempt < TELEGRAM_MAX_ATTEMPTS:
                sleep(_listener_retry_delay(None, attempt))
                continue
            safe_error = str(exc).replace(token, "[REDACTED]")
            print(f"Error sending Telegram message: {safe_error}")
            return False

        if response.status_code == 200:
            try:
                response_payload = response.json()
            except (AttributeError, ValueError):
                response_payload = None
            if isinstance(response_payload, dict) and response_payload.get("ok") is True:
                return True
            print("Error sending Telegram message: invalid response")
            return False

        if response.status_code in TRANSIENT_HTTP_STATUSES and attempt < TELEGRAM_MAX_ATTEMPTS:
            sleep(_listener_retry_delay(response, attempt))
            continue
        print(f"Error sending Telegram message: HTTP {response.status_code}")
        return False
    return False

def run_company_report_command(text):
    try:
        from app.company_reports.live_command_router import CompanyReportCommandError, dispatch_company_report_command
    except ImportError:
        return "Rapor servisi bu ortamda hazır değil; toplantı komutları yine kullanılabilir."

    try:
        result = dispatch_company_report_command(text, send=True)
    except CompanyReportCommandError as exc:
        return f"Rapor üretilemedi: {str(exc)[:200] or 'unknown error'}"
    return {"ok": True, "response": result.response, "matched": result.matched}

def run_report_session_status():
    try:
        from app.company_reports.portal_browser_worker import PortalBrowserWorker
        worker = PortalBrowserWorker.instance()
        ready = asyncio.run(worker.session_ready())
    except Exception:
        ready = False
    return "✅ SYS oturumu aktif." if ready else "⚠️ SYS oturumu yok / MFA gerekiyor."

def run_prepare_report_session():
    try:
        from app.company_reports.portal_browser_worker import PortalBrowserWorker
        worker = PortalBrowserWorker.instance()
        result = worker.prepare_and_check_session(headed=True)
        if isinstance(result, dict) and result.get("session_ready"):
            return "✅ SYS oturumu hazır."
        return "⚠️ SYS oturumu yok / MFA gerekiyor."
    except Exception as e:
        return f"SYS oturumu hazırlanamadi: {str(e)[:200] or 'unknown error'}"

def run_digest(upcoming=False, mode=None):
    try:
        if mode is None:
            exit_code = execute_digest(upcoming=upcoming)
        else:
            exit_code = execute_digest(mode=mode)
    except DigestAlreadyRunning:
        return "Özet şu anda hazırlanıyor. Lütfen biraz sonra tekrar deneyin."
    except Exception as exc:
        return f"Özet çalıştırılamadı: {str(exc)[:200] or 'bilinmeyen hata'}"

    if exit_code != 0:
        return f"Özet çalıştırılamadı (çıkış kodu: {exit_code})."

    if mode == "weekly":
        return "Haftalık toplantı özeti Telegram sohbetinize gönderildi."
    if mode == "attention":
        return "Dikkat gerektiren toplantılar Telegram sohbetinize gönderildi."
    if upcoming:
        return "Bugün ve sonraki toplantı özeti Telegram sohbetinize gönderildi."
    return "Bugünün toplantı özeti Telegram sohbetinize gönderildi."


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
    if normalized in {"komutlar", "help"} or normalized.startswith(("servis", "fatura")):
        registry_result = run_company_report_command(text)
        if isinstance(registry_result, str):
            send_message(token, chat_id, registry_result)
        elif (
            isinstance(registry_result, dict)
            and registry_result.get("response")
            and normalized in {"komutlar", "help"}
        ):
            send_message(token, chat_id, registry_result["response"])
        return

    if normalized in {"session", "sys_session"}:
        send_message(token, chat_id, run_report_session_status())
        return

    if normalized == "session_hazirla":
        send_message(token, chat_id, "SYS oturumu hazırlanıyor. MFA tamamlayın.")
        send_message(token, chat_id, run_prepare_report_session())
        return

    command = (text or "").strip().lower()
    if command in {"/toplantilar", "/toplantılar", "/bugun", "/bugün", "/mail", "/unread"}:
        print("Command received: /toplantilar or /bugun")
        send_message(token, chat_id, run_digest())
    elif command in {
        "/gelecek_toplantilar",
        "/gelecek_toplantılar",
        "/toplantilar_gelecek",
        "/toplantılar_gelecek",
        "/sonraki_toplantilar",
        "/sonraki_toplantılar",
    }:
        print("Command received: /gelecek_toplantilar")
        send_message(token, chat_id, run_digest(upcoming=True))
    elif command in {"/hafta", "/haftalik", "/haftalık", "/week"}:
        print("Command received: /hafta")
        send_message(token, chat_id, run_digest(mode="weekly"))
    elif command in {"/dikkat", "/attention"}:
        print("Command received: /dikkat")
        send_message(token, chat_id, run_digest(mode="attention"))
    elif command in {"/durum", "/status"}:
        print("Command received: /durum")
        send_message(
            token,
            chat_id,
            "✅ Toplantı özeti listener'ı ve hatırlatma desteği çalışıyor.",
        )


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
    print("Starting Telegram Mail Listener...")
    try:
        env = load_env()
        token = env.get("TELEGRAM_BOT_TOKEN")
        chat_id = env.get("TELEGRAM_CHAT_ID")
        allowed_user_id = (env.get("TELEGRAM_ALLOWED_USER_ID") or "").strip() or None
        
        if not token or not chat_id:
            print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in env file.")
            return

        state_file = _update_state_file(env)
        try:
            processed_update_ids = _load_processed_update_ids(state_file)
        except TelegramUpdateStateError as exc:
            print(f"Fatal error in listener: {exc}")
            return

        offset = 0
        while True:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"offset": offset, "timeout": 30}
            try:
                response = requests.get(url, params=params, timeout=35)
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
                print(f"Fatal error in listener: {exc}")
                return
            except Exception as e:
                safe_error = str(e).replace(token, "[REDACTED]")
                print(f"Polling error: {safe_error}")
                time.sleep(5)
                
    except Exception as e:
        print(f"Fatal error in listener: {e}")

if __name__ == "__main__":
    main()
