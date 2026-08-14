import os
import subprocess
import requests
import time
import warnings
import sys
import asyncio
from pathlib import Path
from datetime import datetime

# Suppress urllib3 NotOpenSSLWarning
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(
    os.environ.get(
        "TELEGRAM_ENV_FILE",
        str(Path.home() / ".hermes_local_automation/telegram.env"),
    )
).expanduser()
MAIN_SCRIPT = PROJECT_ROOT / "main.py"
LOCK_FILE = Path("/tmp/mail_unread_digest.lock")
COMPANY_REPORT_ROOT = Path(
    os.environ.get(
        "COMPANY_REPORT_ROOT",
        str(Path.home() / "Projects" / "company_reporting_hub"),
    )
).expanduser()
if str(COMPANY_REPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPANY_REPORT_ROOT))

def load_env():
    env = {}
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Env file not found at {ENV_FILE}")
    with open(ENV_FILE, "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip("'\"")
    return env

def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

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

def run_digest(upcoming=False):
    if LOCK_FILE.exists():
        return "Digest is already running. Please wait."
    
    try:
        LOCK_FILE.touch()
        # Run the digest with the same interpreter as the listener.
        command = [sys.executable, str(MAIN_SCRIPT)]
        if upcoming:
            command.append("--upcoming")
        subprocess.run(command, check=True)
        if upcoming:
            return "Bugün ve sonraki toplantı özeti Telegram sohbetinize gönderildi."
        return "Bugünün toplantı özeti Telegram sohbetinize gönderildi."
    except subprocess.CalledProcessError as e:
        return f"Error running digest: {e}"
    finally:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()

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
                    if normalized in {"komutlar", "help", "/komutlar", "/help"} or normalized.startswith(("servis", "fatura")):
                        registry_result = run_company_report_command(text)
                        if isinstance(registry_result, str):
                            send_message(token, chat_id, registry_result)
                        elif isinstance(registry_result, dict) and registry_result.get("response") and normalized in {"komutlar", "help", "/komutlar", "/help"}:
                            send_message(token, chat_id, registry_result["response"])
                        continue

                    if normalized in {"session", "/session", "sys_session", "/sys_session"}:
                        send_message(token, chat_id, run_report_session_status())
                        continue

                    if normalized in {"session_hazirla", "/session_hazirla"}:
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
                    elif command in {"/durum", "/status"}:
                        print("Command received: /durum")
                        send_message(token, chat_id, "✅ Günlük toplantı özeti listener'ı çalışıyor.")
                    
            except Exception as e:
                safe_error = str(e).replace(token, "[REDACTED]")
                print(f"Polling error: {safe_error}")
                time.sleep(5)
                
    except Exception as e:
        print(f"Fatal error in listener: {e}")

if __name__ == "__main__":
    main()
