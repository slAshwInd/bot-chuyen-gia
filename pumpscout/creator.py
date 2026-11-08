"""S6 creator history (task T-CREATOR).

A creator with one confirmed prior rug is blacklisted permanently
(technical false positives may be cleared by the operator after 30 days;
that is an operator action, never automatic). The store lives in the
runtime data dir as JSON and is written atomically (temp + replace).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class CreatorStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def record(self, creator: str, *, launch: bool = False, rug: bool = False) -> None:
        rec = self._data.setdefault(creator, {"launches": 0, "rugs": 0})
        if launch:
            rec["launches"] += 1
        if rug:
            rec["rugs"] += 1
        self._save()

    def is_blacklisted(self, creator: str, after_rugs: int) -> bool:
        return self._data.get(creator, {}).get("rugs", 0) >= after_rugs

    def stats(self, creator: str) -> tuple[int, int]:
        rec = self._data.get(creator, {})
        return rec.get("rugs", 0), rec.get("launches", 0)

    def blacklisted_set(self, after_rugs: int) -> set[str]:
        return {c for c, r in self._data.items() if r.get("rugs", 0) >= after_rugs}
