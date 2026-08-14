"""Telegram delivery adapter."""

import requests

from ..config import load_env, log


def send_telegram(message):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log("Missing Telegram credentials in env file.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i + 3500] for i in range(0, len(message), 3500)]
    success = True
    for index, chunk in enumerate(chunks):
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": chunk},
                timeout=15,
            )
            if response.status_code != 200:
                log(f"Telegram Error (Chunk {index + 1}/{len(chunks)}): {response.status_code}")
                success = False
        except Exception as exc:
            log(f"Network error sending chunk {index + 1}: {exc}")
            success = False
    return success
