import os
import subprocess
import json
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

ENV_FILE = Path.home() / ".hermes_local_automation/telegram.env"
MAIN_SCRIPT = Path("/Users/ertugrulcetinkaya/Projects/mail_unread_digest/main.py")
LOCK_FILE = Path("/tmp/mail_unread_digest.lock")

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

def run_digest():
    if LOCK_FILE.exists():
        return "Digest is already running. Please wait."
    
    try:
        LOCK_FILE.touch()
        # Run the main script and capture output if needed, but we just need it to execute
        subprocess.run(["python3", str(MAIN_SCRIPT)], check=True)
        return "Unread mail digest has been sent to your chat."
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

                    if text == "/mail" or text == "/unread":
                        print("Command received: /mail or /unread")
                        result = run_digest()
                        send_message(token, chat_id, result)
                    elif text == "/status":
                        print("Command received: /status")
                        send_message(token, chat_id, "✅ Mail digest listener is running and active.")
                    
            except Exception as e:
                print(f"Polling error: {e}")
                time.sleep(5)
                
    except Exception as e:
        print(f"Fatal error in listener: {e}")

if __name__ == "__main__":
    main()
