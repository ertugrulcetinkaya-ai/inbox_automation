import os
import subprocess
import json
import requests
import warnings
import re
import argparse
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
FIELD_DELIMITER = "__MAIL_DIGEST_FIELD__"

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

def sanitize(text):
    if text is None:
        return ""
    # Replace CR/LF/TAB with spaces
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    # Remove ASCII control characters (0-31) except space
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch == " ")
    # Remove zero-width/invisible spam padding characters
    # \u200b: Zero Width Space, \u200c: Zero Width Non-Joiner, \u200d: Zero Width Joiner, \uFEFF: BOM
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    # Collapse repeated whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text

def fetch_mail():
    try:
        # AppleScript now outputs records to stdout (via 'log' or 'return')
        # Note: osascript 'log' goes to stderr in some versions, but usually we can capture it.
        # To be safe and consistent, we use a simple return of a joined string if possible, 
        # but the requested design was line-based records.
        # Since AppleScript 'log' is used in the script, we capture stderr as well.
        result = subprocess.run(
            ["osascript", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            check=False # osascript can return non-zero for some warnings
        )
        
        # Combine stdout and stderr because 'log' in AppleScript often goes to stderr
        raw_output = result.stdout + "\n" + result.stderr
        
        records = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line or FIELD_DELIMITER not in line:
                continue
            
            # Remove AppleScript 'log:' prefix if present
            if line.startswith("log:"):
                line = line[4:].strip()
            
            parts = line.split(FIELD_DELIMITER)
            if len(parts) >= 5:
                # account | mailbox | sender | subject | date | [snippet]
                records.append({
                    "account": sanitize(parts[0]),
                    "mailbox": sanitize(parts[1]),
                    "sender": sanitize(parts[2]),
                    "subject": sanitize(parts[3]),
                    "date": sanitize(parts[4]),
                    "snippet": sanitize(parts[5]) if len(parts) > 5 else ""
                })
        
        return records
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
    chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
    
    success = True
    for idx, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        try:
            response = requests.post(url, json=payload)
            if response.status_code != 200:
                log(f"Telegram Error (Chunk {idx+1}/{len(chunks)}): {response.status_code}")
                success = False
        except Exception as e:
            log(f"Network error sending chunk {idx+1}: {e}")
            success = False
    return success

def format_digest(records):
    if not records:
        return "No unread mail found."

    # Group by account/mailbox
    grouped = {}
    for r in records:
        key = (r["account"], r["mailbox"])
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)

    lines = ["📬 Mail Unread Digest\n"]
    has_unread = False

    for (acc_name, mb_name), msgs in grouped.items():
        has_unread = True
        lines.append(f"{acc_name} > {mb_name} ({len(msgs)})")
        
        for msg in msgs:
            sender = msg["sender"][:30] or "Unknown"
            subject = msg["subject"][:60] or "No Subject"
            date = msg["date"] or "Unknown"
            lines.append(f"• {sender} | {date}\n  {subject}")
            lines.append("") # Spacer

    if not has_unread:
        return "No unread mail found."

    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but do not send")
    args = parser.parse_args()

    log("START mail unread digest")
    
    log("Fetching unread emails")
    records = fetch_mail()
    if records is None:
        log("Failed to retrieve data.")
        log("DONE mail unread digest (FAILED)")
        return

    log(f"Processed {len(records)} unread messages")

    message = format_digest(records)
    
    if args.dry_run:
        log("Dry-run mode: printing digest to stdout")
        print("\n--- DRY RUN DIGEST START ---\n")
        print(message)
        print("\n--- DRY RUN DIGEST END ---")
    else:
        log("Sending digest to Telegram")
        if send_telegram(message):
            log("Telegram send success")
        else:
            log("Telegram send failure")
        
    log("DONE mail unread digest")

if __name__ == "__main__":
    main()
