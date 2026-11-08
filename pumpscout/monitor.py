"""Read-only monitor (mirrors the BaseAster monitor child).

Reads only the heartbeat file and the ledger; never trades, never
touches the pipeline. A heartbeat older than 60 seconds is severity-1
because in live phases the bot IS the stop - there is no exchange-side
order protecting an open position. Alerts go to Telegram when env
credentials exist, with a cooldown and a recovery notice.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .ledger import Ledger
from .tg import send_message

STALE_SECONDS = 60.0
COOLDOWN_SECONDS = 600.0


def check(data_dir: Path, state: dict) -> str:
    now = time.time()
    hb_file = data_dir / "heartbeat.json"
    age = None
    if hb_file.exists():
        try:
            hb = json.loads(hb_file.read_text(encoding="utf-8"))
            age = now - float(hb.get("ts", 0))
        except Exception:
            age = None
    alerting = age is None or age > STALE_SECONDS
    last_alert = state.get("last_alert", 0.0)
    was_alerting = state.get("alerting", False)
    if alerting and now - last_alert >= COOLDOWN_SECONDS:
        msg = ("[PumpScout] CANH BAO CAP 1: Scout khong co heartbeat moi "
               f"(age={age if age is None else round(age)}s). "
               "Kiem tra process/systemd ngay.")
        send_message(msg)
        print(msg)
        state["last_alert"] = now
    if was_alerting and not alerting:
        msg = "[PumpScout] Da phuc hoi: heartbeat Scout tro lai binh thuong."
        send_message(msg)
        print(msg)
    state["alerting"] = alerting
    return "alert" if alerting else "ok"


def funnel_report(data_dir: Path) -> str:
    ledger = Ledger(data_dir / "ledger.db")
    try:
        rows = ledger.funnel_counts(time.time() - 86400)
    finally:
        ledger.close()
    lines = ["[PumpScout] Phieu loc 24h:"]
    lines += [f"  {code}: {n}" for code, n in rows] or ["  (chua co quyet dinh)"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--funnel", action="store_true")
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    if args.funnel:
        print(funnel_report(data_dir))
        return
    state_file = data_dir / "monitor_state.json"
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
    while True:
        check(data_dir, state)
        state_file.write_text(json.dumps(state), encoding="utf-8")
        if args.once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
