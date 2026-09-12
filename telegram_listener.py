import random
import requests
import time
import warnings
import asyncio

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


def _listener_retry_delay(response, attempt):
    if getattr(response, "status_code", None) == 429:
        headers = getattr(response, "headers", {}) or {}
        value = None
        if hasattr(headers, "get"):
            value = headers.get("Retry-After") or headers.get("retry-after")
        try:
            return min(30.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            pass
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

def main():
    print("Starting Telegram Mail Listener...")
    try:
        env = load_env()
        token = env.get("TELEGRAM_BOT_TOKEN")
        chat_id = env.get("TELEGRAM_CHAT_ID")
        
        if not token or not chat_id:
            print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in env file.")
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
                    offset = update["update_id"] + 1
                    if "message" not in update:
                        continue
                    
                    msg = update["message"]
                    chat_id_msg = str(msg.get("chat", {}).get("id"))
                    text = msg.get("text", "")

                    # Only accept commands from the authorized chat ID
                    if chat_id_msg != str(chat_id):
                        continue

                    normalized = (text or "").strip().lower().lstrip("/")
                    if normalized in {"komutlar", "help"} or normalized.startswith(("servis", "fatura")):
                        registry_result = run_company_report_command(text)
                        if isinstance(registry_result, str):
                            send_message(token, chat_id, registry_result)
                        elif isinstance(registry_result, dict) and registry_result.get("response") and normalized in {"komutlar", "help"}:
                            send_message(token, chat_id, registry_result["response"])
                        continue

                    if normalized in {"session", "sys_session"}:
                        send_message(token, chat_id, run_report_session_status())
                        continue

                    if normalized == "session_hazirla":
                        send_message(token, chat_id, "SYS oturumu hazırlanıyor. MFA tamamlayın.")
                        send_message(token, chat_id, run_prepare_report_session())
                        continue

                    command = (text or "").strip().lower()

                    if command in {"/toplantilar", "/toplantılar", "/bugun", "/bugün", "/mail", "/unread"}:
                        print("Command received: /toplantilar or /bugun")
                        result = run_digest()
                        send_message(token, chat_id, result)
                    elif command in {
                        "/gelecek_toplantilar",
                        "/gelecek_toplantılar",
                        "/toplantilar_gelecek",
                        "/toplantılar_gelecek",
                        "/sonraki_toplantilar",
                        "/sonraki_toplantılar",
                    }:
                        print("Command received: /gelecek_toplantilar")
                        result = run_digest(upcoming=True)
                        send_message(token, chat_id, result)
                    elif command in {"/hafta", "/haftalik", "/haftalık", "/week"}:
                        print("Command received: /hafta")
                        result = run_digest(mode="weekly")
                        send_message(token, chat_id, result)
                    elif command in {"/dikkat", "/attention"}:
                        print("Command received: /dikkat")
                        result = run_digest(mode="attention")
                        send_message(token, chat_id, result)
                    elif command in {"/durum", "/status"}:
                        print("Command received: /durum")
                        send_message(
                            token,
                            chat_id,
                            "✅ Toplantı özeti listener'ı ve hatırlatma desteği çalışıyor.",
                        )
                    
            except Exception as e:
                safe_error = str(e).replace(token, "[REDACTED]")
                print(f"Polling error: {safe_error}")
                time.sleep(5)
                
    except Exception as e:
        print(f"Fatal error in listener: {e}")

if __name__ == "__main__":
    main()
