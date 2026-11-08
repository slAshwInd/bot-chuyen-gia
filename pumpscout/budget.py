"""Provider request budgeting (quota guard).

The Helius free tier allows ~1M credits per 30 days (~33k/day). Two
buckets are tracked separately because the enhanced-transactions API
(api.helius.xyz) is a different, scarcer resource than plain RPC:
  - "rpc": getMultipleAccounts / getTokenLargestAccounts (budget 30k/day)
  - "api": enhanced transactions fallback for trade history (2k/day)

The counter is persisted in the runtime data dir and is fail-closed:
when a bucket is spent, the corresponding read stops and tokens are
decided blind instead of silently burning quota or passing unchecked.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


class RequestBudget:
    def __init__(self, path: str | Path, limits: dict[str, int]):
        self.path = Path(path)
        self.limits = dict(limits)
        self.day = ""
        self.used: dict[str, int] = {k: 0 for k in limits}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.day = saved.get("day", "")
                for k in self.limits:
                    self.used[k] = int(saved.get("used", {}).get(k, 0))
            except Exception:
                self.day = ""
        self._roll(time.time())

    def _roll(self, now: float) -> None:
        day = time.strftime("%Y%m%d", time.gmtime(now))
        if day != self.day:
            self.day = day
            self.used = {k: 0 for k in self.limits}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"day": self.day, "used": self.used}, fh)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def allow(self, bucket: str, cost: int, now: float | None = None) -> bool:
        self._roll(now or time.time())
        return self.used.get(bucket, 0) + cost <= self.limits.get(bucket, 0)

    def spend(self, bucket: str, cost: int, now: float | None = None) -> None:
        self._roll(now or time.time())
        self.used[bucket] = self.used.get(bucket, 0) + cost
        self._save()

    def remaining(self, bucket: str, now: float | None = None) -> int:
        self._roll(now or time.time())
        return max(0, self.limits.get(bucket, 0) - self.used.get(bucket, 0))
