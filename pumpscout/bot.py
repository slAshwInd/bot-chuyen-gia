"""PumpScout Scout process (shadow + paper modes).

Wires discovery -> pipeline -> ledger/recorder, runs the fate labeler,
maintains a regime snapshot from observed stream statistics and writes
an atomic heartbeat the read-only monitor watches. In paper mode the
executor opens simulated positions from accepted decisions and the
supervisor closes them by the S10 rules. This process cannot sign or
send anything: there is no live executor in this build.

Run:  python -m pumpscout.bot --config pumpscout.toml --data-dir /path/runtime
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__
from .budget import RequestBudget
from .config import load_config
from .creator import CreatorStore
from .discovery import stream_tokens
from .enrichment import Enricher
from .fate import run_fate_pass
from .ledger import Ledger
from .pipeline import ShadowPipeline
from .potential import RegimeSnapshot
from .recorder import Recorder
from .risk import DayState


def write_heartbeat(data_dir: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(data_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, data_dir / "heartbeat.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Scout:
    def __init__(self, cfg, data_dir: Path):
        self.cfg = cfg
        self.data_dir = data_dir
        self.ledger = Ledger(data_dir / "ledger.db")
        self.recorder = Recorder(data_dir)
        self.creators = CreatorStore(data_dir / "creators.json")
        from .surrogate import resolve_rpc_url
        rpc_url, rpc_status = resolve_rpc_url(cfg.rpc_url)
        self.budget = RequestBudget(
            data_dir / "rpc_budget.json",
            {"rpc": cfg.rpc_daily_budget, "api": cfg.helius_api_daily_budget})
        self.enricher = Enricher(rpc_url, budget=self.budget)
        self.ledger.event(time.time(), "rpc_endpoint", None,
                          {"status": rpc_status})
        self.pipeline = ShadowPipeline(cfg, self.ledger, self.recorder,
                                       self.creators, self.enricher,
                                       self.budget)
        self.executor = None
        self.supervisor = None
        if cfg.mode == "paper":
            from .executor import Executor
            from .supervisor import Supervisor
            self.executor = Executor(cfg, self.ledger, self.enricher.rpc,
                                     self.budget)
            self.supervisor = Supervisor(cfg, self.executor, rpc_url,
                                         self.enricher.rpc, self.budget)
        self.stop = asyncio.Event()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.launches_last_hour: list[float] = []
        self.migrations_last_hour: list[float] = []
        self.regime = RegimeSnapshot(ts=0.0, score=0.0, launches_per_hour=0.0,
                                     graduations_per_hour=0.0)
        self.decisions_today = 0

    def _on_token(self, tok) -> None:
        self.launches_last_hour.append(tok.seen_ts)
        self.pipeline.on_token(tok)

    def _on_event(self, kind: str, payload: dict) -> None:
        now = time.time()
        self.ledger.event(now, kind, payload.get("mint"), payload)
        self.recorder.record("stream_event", now, {"event": kind, **payload})
        if kind == "migration":
            self.migrations_last_hour.append(now)

    def _refresh_regime(self, now: float) -> None:
        hour = 3600.0
        self.launches_last_hour = [t for t in self.launches_last_hour if now - t <= hour]
        self.migrations_last_hour = [t for t in self.migrations_last_hour if now - t <= hour]
        launches = len(self.launches_last_hour)
        grads = len(self.migrations_last_hour)
        ratio = (grads / launches) if launches else 0.0
        # Healthy pump.fun hours graduate roughly 1-2% of launches; map a
        # 2% ratio to a fully open gate, 0% to closed. Heuristic, P1 only.
        score = max(0.0, min(1.0, ratio / 0.02))
        self.regime = RegimeSnapshot(ts=now, score=score,
                                     launches_per_hour=float(launches),
                                     graduations_per_hour=float(grads))

    def _regime_ok(self, now: float) -> bool:
        from .potential import regime_open
        return regime_open(self.regime, now, self.cfg.regime_stale_seconds)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        stream = asyncio.create_task(stream_tokens(
            self.cfg.pumpportal_ws, self._on_token, self._on_event, self.stop))
        sup_task = None
        if self.supervisor is not None:
            sup_task = asyncio.create_task(self.supervisor.run())
        last_fate = time.time()
        try:
            while not self.stop.is_set():
                now = time.time()
                self._refresh_regime(now)
                if self.executor is not None:
                    day = self.executor.day_state(now)
                else:
                    day = DayState()  # shadow: brakes are informational
                try:
                    decisions = await loop.run_in_executor(
                        self.pool, self.pipeline.sweep, now, day,
                        self._regime_ok(now))
                    self.decisions_today += len(decisions)
                    if self.executor is not None:
                        for dec in decisions:
                            if not dec.accepted:
                                continue
                            snap = self.ledger.latest_analysis(dec.mint) or {}
                            snap_price = None
                            if snap.get("virtual_sol") and snap.get("virtual_token"):
                                snap_price = snap["virtual_sol"] / snap["virtual_token"]
                            await loop.run_in_executor(
                                self.pool, self.executor.enter, dec, snap_price)
                except Exception as exc:  # a bad sweep must not kill the scout
                    self.ledger.event(now, "sweep_error", None,
                                      {"error": type(exc).__name__})
                if now - last_fate > 600:
                    last_fate = now
                    try:
                        await loop.run_in_executor(
                            self.pool, run_fate_pass, self.ledger,
                            self.enricher.rpc, None, self.budget,
                            self.cfg.fate_max_labels_per_horizon,
                            self.cfg.fate_abort_after_failures,
                            self.cfg.fate_defer_after_failures)
                    except Exception as exc:
                        self.ledger.event(now, "fate_error", None,
                                          {"error": type(exc).__name__})
                hb = {
                    "ts": now, "version": __version__, "mode": self.cfg.mode,
                    "buffered": len(self.pipeline.buffer),
                    "regime_score": round(self.regime.score, 3),
                    "decisions_total": self.decisions_today,
                    "rpc_budget_remaining": self.budget.remaining("rpc"),
                    "api_budget_remaining": self.budget.remaining("api"),
                }
                if self.executor is not None:
                    hb.update(self.executor.summary(now))
                    hb["supervisor_ws_ok"] = bool(
                        self.supervisor and self.supervisor.ws_healthy)
                write_heartbeat(self.data_dir, hb)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.stop.set()
            stream.cancel()
            if sup_task is not None:
                sup_task.cancel()
            self.recorder.close()
            self.ledger.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config, data_dir)
    if cfg.mode not in ("shadow", "paper"):
        raise SystemExit("this build runs shadow or paper mode only; "
                         "there is no live executor")
    scout = Scout(cfg, data_dir)
    try:
        asyncio.run(scout.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
