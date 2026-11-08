"""S0 discovery: PumpPortal WebSocket stream of new tokens + migrations.

Free subscriptions only in P1 (subscribeNewToken / subscribeMigration);
per-token trade streams are metered and are only opened from P2 on for
tokens that passed S3. Reconnects with bounded backoff and records a
WS_GAP event so replay data stays honest about blind windows.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict

import websockets

from .models import NewToken


class SeenSet:
    """Bounded dedupe set (oldest evicted first)."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._set: OrderedDict[str, None] = OrderedDict()

    def add(self, mint: str) -> bool:
        if mint in self._set:
            return False
        self._set[mint] = None
        while len(self._set) > self.capacity:
            self._set.popitem(last=False)
        return True


def parse_new_token(msg: dict, seen_ts: float) -> NewToken | None:
    if msg.get("txType") != "create" or not msg.get("mint"):
        return None
    created = msg.get("createdTimestamp") or msg.get("timestamp")
    return NewToken(
        mint=msg["mint"],
        creator=msg.get("creator") or msg.get("traderPublicKey") or "",
        name=msg.get("name") or "",
        symbol=msg.get("symbol") or "",
        uri=msg.get("uri") or "",
        created_ts=(float(created) / 1000.0) if created else seen_ts,
        seen_ts=seen_ts,
        initial_buy_sol=(msg.get("initialBuy") or msg.get("solAmount") or 0) / 1e9
        if (msg.get("initialBuy") or msg.get("solAmount")) else 0.0,
        curve_pct=None,
    )


async def stream_tokens(ws_url: str, on_token, on_event,
                        stop: asyncio.Event) -> None:
    """Run the discovery stream forever (until stop is set)."""
    backoff = 2.0
    while not stop.is_set():
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                on_event("ws_connected", {})
                backoff = 2.0
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    tok = parse_new_token(msg, time.time())
                    if tok is not None:
                        on_token(tok)
                    elif msg.get("txType") == "migrate" and msg.get("mint"):
                        on_event("migration", {"mint": msg["mint"]})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # connection dropped: record the gap, retry
            on_event("ws_gap", {"error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)
