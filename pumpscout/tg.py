"""Telegram notifications via environment-provided bot credentials.

Credentials live only in the process environment of the runtime machine
(PUMPSCOUT_TG_BOT_TOKEN / PUMPSCOUT_TG_CHAT_ID) - never in the repo, the
config file or the ledger. Without them, sending is a logged no-op.
"""
from __future__ import annotations

import os

import requests


def send_message(text: str, timeout: float = 8.0) -> bool:
    token = os.environ.get("PUMPSCOUT_TG_BOT_TOKEN", "")
    chat = os.environ.get("PUMPSCOUT_TG_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=timeout)
        return r.ok
    except Exception:
        return False
