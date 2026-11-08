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
CREATE TABLE IF NOT EXISTS paper_fills(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, mint TEXT NOT NULL,
  kind TEXT NOT NULL, reason TEXT, price REAL, tokens REAL,
  sol_amount REAL, fee_sol REAL, pnl_sol REAL);
CREATE TABLE IF NOT EXISTS paper_positions(
  mint TEXT PRIMARY KEY, entry_ts REAL NOT NULL, entry_price REAL NOT NULL,
  tokens_initial REAL NOT NULL, tokens_held REAL NOT NULL,
  cost_total_sol REAL NOT NULL, realised_sol REAL NOT NULL DEFAULT 0,
  tp_done INTEGER NOT NULL DEFAULT 0, peak_price REAL NOT NULL,
  last_price REAL, status TEXT NOT NULL DEFAULT 'open',
  exit_ts REAL, exit_reason TEXT);
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

    # ---- paper trading (P2): fills are append-only; positions are the book
    def add_fill(self, ts: float, mint: str, kind: str, reason: str | None,
                 price: float | None, tokens: float | None,
                 sol_amount: float | None, fee_sol: float | None,
                 pnl_sol: float | None) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO paper_fills(ts,mint,kind,reason,price,tokens,"
                "sol_amount,fee_sol,pnl_sol) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts, mint, kind, reason, price, tokens, sol_amount,
                 fee_sol, pnl_sol))
            self.db.commit()

    def save_position(self, pos: dict) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO paper_positions(mint,entry_ts,entry_price,"
                "tokens_initial,tokens_held,cost_total_sol,realised_sol,"
                "tp_done,peak_price,last_price,status,exit_ts,exit_reason)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(mint) DO UPDATE SET tokens_held=excluded.tokens_held,"
                " realised_sol=excluded.realised_sol, tp_done=excluded.tp_done,"
                " peak_price=excluded.peak_price, last_price=excluded.last_price,"
                " status=excluded.status, exit_ts=excluded.exit_ts,"
                " exit_reason=excluded.exit_reason",
                (pos["mint"], pos["entry_ts"], pos["entry_price"],
                 pos["tokens_initial"], pos["tokens_held"],
                 pos["cost_total_sol"], pos["realised_sol"],
                 int(pos["tp_done"]), pos["peak_price"], pos.get("last_price"),
                 pos["status"], pos.get("exit_ts"), pos.get("exit_reason")))
            self.db.commit()

    def load_open_positions(self) -> list[dict]:
        with self._lock:
            cur = self.db.execute(
                "SELECT * FROM paper_positions WHERE status='open'")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def paper_day_aggregates(self, day_start_ts: float) -> dict:
        """Entries count, gross spent and realised PnL since day_start_ts."""
        with self._lock:
            entries, spent = self.db.execute(
                "SELECT COUNT(*), COALESCE(SUM(sol_amount),0) FROM paper_fills"
                " WHERE kind='entry' AND ts >= ?", (day_start_ts,)).fetchone()
            realised = self.db.execute(
                "SELECT COALESCE(SUM(pnl_sol),0) FROM paper_fills"
                " WHERE kind IN ('exit_partial','exit_final') AND ts >= ?",
                (day_start_ts,)).fetchone()[0]
            total_realised = self.db.execute(
                "SELECT COALESCE(SUM(pnl_sol),0) FROM paper_fills"
                " WHERE kind IN ('exit_partial','exit_final')").fetchone()[0]
        return {"entries": int(entries), "spent_sol": float(spent),
                "realised_today_sol": float(realised),
                "realised_total_sol": float(total_realised)}

    def funnel_counts(self, since_ts: float) -> list[tuple[str, int]]:
        with self._lock:
            return self.db.execute(
                "SELECT reason_code, COUNT(*) FROM decisions WHERE ts >= ?"
                " GROUP BY reason_code ORDER BY COUNT(*) DESC", (since_ts,)).fetchall()
