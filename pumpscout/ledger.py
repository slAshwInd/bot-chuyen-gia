"""Append-only SQLite ledger - the single source of truth.

Tables mirror design section 4. Rows are never updated or deleted by the
trading code; fate labels and daily stats are separate append-only
tables so history stays auditable and replayable.

The Scout calls the ledger from executor threads (analysis sweep, fate
pass) while the event loop records stream events, so one connection is
shared with check_same_thread=False and every call is serialised by a
lock - SQLite stays the single writer either way.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
  kind TEXT NOT NULL, mint TEXT, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(
  mint TEXT PRIMARY KEY, creator TEXT NOT NULL, name TEXT, symbol TEXT,
  uri TEXT, created_ts REAL, first_seen_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts REAL NOT NULL,
  snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts REAL NOT NULL,
  stage TEXT NOT NULL, accepted INTEGER NOT NULL, reason_code TEXT NOT NULL,
  score REAL, breakdown TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS fate_labels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL,
  horizon TEXT NOT NULL, ts REAL NOT NULL, label TEXT NOT NULL,
  price_change REAL, detail TEXT,
  UNIQUE(mint, horizon));
CREATE TABLE IF NOT EXISTS daily_stats(
  day TEXT NOT NULL, key TEXT NOT NULL, value REAL NOT NULL,
  PRIMARY KEY(day, key));
"""


class Ledger:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def event(self, ts: float, kind: str, mint: str | None, payload: dict) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO events(ts,kind,mint,payload) VALUES(?,?,?,?)",
                (ts, kind, mint, json.dumps(payload, ensure_ascii=False)))
            self.db.commit()

    def add_token(self, mint: str, creator: str, name: str, symbol: str,
                  uri: str, created_ts: float | None, first_seen_ts: float) -> bool:
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO tokens VALUES(?,?,?,?,?,?,?)",
                (mint, creator, name, symbol, uri, created_ts, first_seen_ts))
            self.db.commit()
            return cur.rowcount > 0

    def add_analysis(self, mint: str, ts: float, snapshot: dict) -> None:
        with self._lock:
            self.db.execute("INSERT INTO analyses(mint,ts,snapshot) VALUES(?,?,?)",
                            (mint, ts, json.dumps(snapshot, ensure_ascii=False)))
            self.db.commit()

    def add_decision(self, mint: str, ts: float, stage: str, accepted: bool,
                     reason_code: str, score: float | None,
                     breakdown: tuple, detail: str = "") -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO decisions(mint,ts,stage,accepted,reason_code,score,breakdown,detail)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (mint, ts, stage, int(accepted), reason_code, score,
                 json.dumps(breakdown), detail))
            self.db.commit()

    def add_fate_label(self, mint: str, horizon: str, ts: float, label: str,
                       price_change: float | None, detail: str = "") -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR IGNORE INTO fate_labels(mint,horizon,ts,label,price_change,detail)"
                " VALUES(?,?,?,?,?,?)", (mint, horizon, ts, label, price_change, detail))
            self.db.commit()

    def tokens_needing_fate(self, horizon_seconds: float, now: float) -> list[str]:
        """Decided tokens old enough for this horizon and not yet labelled."""
        horizon = f"{int(horizon_seconds // 3600)}h"
        with self._lock:
            rows = self.db.execute(
                "SELECT DISTINCT d.mint FROM decisions d"
                " WHERE d.ts + ? <= ? AND d.mint NOT IN"
                " (SELECT mint FROM fate_labels WHERE horizon = ?)",
                (horizon_seconds, now, horizon)).fetchall()
        return [r[0] for r in rows]

    def latest_analysis(self, mint: str) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT snapshot FROM analyses WHERE mint = ? ORDER BY id DESC LIMIT 1",
                (mint,)).fetchone()
        return json.loads(row[0]) if row else None

    def funnel_counts(self, since_ts: float) -> list[tuple[str, int]]:
        with self._lock:
            return self.db.execute(
                "SELECT reason_code, COUNT(*) FROM decisions WHERE ts >= ?"
                " GROUP BY reason_code ORDER BY COUNT(*) DESC", (since_ts,)).fetchall()
