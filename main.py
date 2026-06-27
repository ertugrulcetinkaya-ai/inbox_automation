import os
import subprocess
import json
import requests
import warnings
from datetime import datetime
from pathlib import Path

# Suppress urllib3 NotOpenSSLWarning for launchd logs
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

ENV_FILE = Path.home() / ".hermes_local_automation/telegram.env"
SCRIPT_PATH = Path("/Users/ertugrulcetinkaya/Projects/mail_unread_digest/mail_fetcher.applescript")

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)

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

def fetch_mail():
    try:
        result = subprocess.run(
            ["osascript", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            check=True
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as je:
            log(f"JSON Parsing Error: {je}")
            log_path = Path("logs/mail_raw_output.json")
            log_path.parent.mkdir(exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
            log(f"Raw output dumped to {log_path}")
            return None
    except Exception as e:
        log(f"Error fetching mail: {e}")
        return None

def send_telegram(message):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        log("Missing Telegram credentials in env file.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Split message into chunks of 3500 characters
    chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
    
    success = True
    for idx, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        try:
            response = requests.post(url, json=payload)
            if response.status_code != 200:
                log(f"Telegram Error (Chunk {idx+1}/{len(chunks)}): {response.status_code}")
                log(f"Response Body: {response.text}")
                success = False
        except Exception as e:
            log(f"Network error sending chunk {idx+1}: {e}")
            success = False
    return success

def format_digest(data):
    if not data or not data.get("accounts"):
        return "No unread mail found."

    lines = ["📬 Mail Unread Digest\n"]
    has_unread = False

    for account in data["accounts"]:
        acc_name = account.get("name", "Unknown Account")
        mailboxes = account.get("mailboxes", [])
        
        for mb in mailboxes:
            unread_count = mb.get("unread_count", 0)
            if unread_count == 0:
                continue
            
            has_unread = True
            lines.append(f"{acc_name} > {mb.get('name')} ({unread_count})")
            
            for msg in mb.get("messages", []):
                sender = msg.get("sender", "Unknown")[:30]
                subject = msg.get("subject", "No Subject")[:60]
                date = msg.get("date", "Unknown")
                snippet = msg.get("snippet", "")[:100]
                
                lines.append(f"• {sender} | {date}\n  {subject}\n  {snippet}...")
                lines.append("") # Spacer

    if not has_unread:
        return "No unread mail found."

    return "\n".join(lines)

def main():
    log("START mail unread digest")
    
    log("Fetching unread emails")
    data = fetch_mail()
    if data is None:
        log("Failed to retrieve data.")
        log("DONE mail unread digest (FAILED)")
        return

    # Log processing stats
    accounts = data.get("accounts", [])
    total_mailboxes = sum(len(acc.get("mailboxes", [])) for acc in accounts)
    log(f"Processed {len(accounts)} accounts and {total_mailboxes} mailboxes")

    message = format_digest(data)
    
    log("Sending digest to Telegram")
    if send_telegram(message):
        log("Telegram send success")
    else:
        log("Telegram send failure")
        
    log("DONE mail unread digest")

if __name__ == "__main__":
    main()
