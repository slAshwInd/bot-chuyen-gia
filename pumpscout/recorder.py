"""Recorder: raw stream + decision journal as daily JSONL files.

This recorded data is the ONLY honest backtest source for memecoins
(design section 8): the replay engine consumes exactly these files, so
everything the live pipeline saw must land here, including gaps.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class Recorder:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "recordings"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, object] = {}

    def _file(self, kind: str, ts: float):
        day = time.strftime("%Y%m%d", time.gmtime(ts))
        key = f"{kind}-{day}"
        fh = self._handles.get(key)
        if fh is None:
            fh = open(self.dir / f"{key}.jsonl", "a", encoding="utf-8")
            self._handles[key] = fh
        return fh

    def record(self, kind: str, ts: float, payload: dict) -> None:
        fh = self._file(kind, ts)
        fh.write(json.dumps({"ts": ts, "kind": kind, **payload},
                            ensure_ascii=False) + "\n")
        fh.flush()

    def close(self) -> None:
        for fh in self._handles.values():
            fh.close()
        self._handles.clear()
