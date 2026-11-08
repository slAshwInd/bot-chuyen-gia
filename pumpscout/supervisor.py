"""Position supervisor (S10 transport): watches open positions' bonding
curve accounts and feeds price updates to the paper executor.

Primary transport is a Helius WebSocket account subscription per open
position (design rule for P2: subscriptions, never polling - one polled
position at 3s would burn ~28.8k requests/day). If the socket is down,
a slow RPC fallback (15s, budget-guarded) keeps exits possible; exits
are never blocked by the risk gate, so this path must not die quietly:
its health is exported to the heartbeat.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time

import websockets

from .enrichment import bonding_curve_address
from .executor import Executor, parse_curve

RECONCILE_SECONDS = 5.0
WS_STALE_SECONDS = 45.0
FALLBACK_POLL_SECONDS = 15.0


class Supervisor:
    def __init__(self, cfg, executor: Executor, rpc_url: str, rpc, budget=None):
        self.cfg = cfg
        self.executor = executor
        self.ws_url = rpc_url.replace("https://", "wss://").replace(
            "http://", "ws://")
        self.rpc = rpc
        self.budget = budget
        self.ws_healthy = False
        self.last_msg_ts = 0.0

    async def run(self) -> None:
        poller = asyncio.create_task(self._fallback_poller())
        try:
            while True:
                try:
                    await self._session()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.ws_healthy = False
                await asyncio.sleep(5)
        finally:
            poller.cancel()

    async def _session(self) -> None:
        subs: dict[int, str] = {}      # subscription id -> mint
        pending: dict[int, str] = {}   # request id -> mint
        next_id = 1
        async with websockets.connect(self.ws_url, ping_interval=20,
                                      ping_timeout=20) as ws:
            self.ws_healthy = True
            self.last_msg_ts = time.time()
            while True:
                # Reconcile subscriptions with the open book.
                want = set(self.executor.open_mints())
                have = set(subs.values()) | set(pending.values())
                for mint in want - have:
                    addr = bonding_curve_address(mint)
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": next_id, "method": "accountSubscribe",
                        "params": [addr, {"encoding": "base64",
                                          "commitment": "confirmed"}]}))
                    pending[next_id] = mint
                    next_id += 1
                for sub_id, mint in list(subs.items()):
                    if mint not in want:
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": next_id,
                            "method": "accountUnsubscribe", "params": [sub_id]}))
                        next_id += 1
                        del subs[sub_id]
                try:
                    raw = await asyncio.wait_for(ws.recv(),
                                                 timeout=RECONCILE_SECONDS)
                except asyncio.TimeoutError:
                    if time.time() - self.last_msg_ts > WS_STALE_SECONDS:
                        raise ConnectionError("websocket stale")
                    continue
                self.last_msg_ts = time.time()
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in pending:
                    mint = pending.pop(msg["id"])
                    if isinstance(msg.get("result"), int):
                        subs[msg["result"]] = mint
                    continue
                params = msg.get("params") or {}
                sub_id = params.get("subscription")
                mint = subs.get(sub_id)
                if mint is None:
                    continue
                value = (params.get("result") or {}).get("value") or {}
                data = value.get("data") or []
                if not data:
                    continue
                parsed = parse_curve(base64.b64decode(data[0]))
                if parsed is None:
                    continue
                v_sol, v_tok, real_sol, _complete = parsed
                self.executor.on_price(mint, v_sol, v_tok, real_sol,
                                       time.time())

    async def _fallback_poller(self) -> None:
        """Slow RPC safety net, active only while the socket is unhealthy."""
        while True:
            await asyncio.sleep(FALLBACK_POLL_SECONDS)
            if self.ws_healthy:
                continue
            for mint in self.executor.open_mints():
                if self.budget is not None and not self.budget.allow("rpc", 1):
                    break
                try:
                    got = self.rpc.account_data(bonding_curve_address(mint))
                except Exception:
                    got = None
                if self.budget is not None:
                    self.budget.spend("rpc", 1)
                if got is None:
                    continue
                parsed = parse_curve(got[0])
                if parsed is None:
                    continue
                v_sol, v_tok, real_sol, _c = parsed
                self.executor.on_price(mint, v_sol, v_tok, real_sol,
                                       time.time())
