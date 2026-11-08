"""Paper executor (P2, task T-EXIT/T-COST): turns accepted decisions
into simulated positions and closes them by the S10 exit rules.

Paper only. There is deliberately no signing or sending code in this
module - a live executor is a separate, separately approved build.

Fill model (design section 6): bonding-curve integral quotes, fees
charged as the real route would charge them (pump.fun 1% of trade
value, PumpPortal 0.5%, base fee + Jito tip per transaction), and the
entry executes on a FRESH curve read taken after the decision - the
maker-checker re-verification doubles as the modelled 2s delay, and a
token whose price already collapsed or ran away is skipped.
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from . import curve_math
from .config import Config
from .enrichment import LAMPORTS, TOKEN_DECIMALS, bonding_curve_address
from .ledger import Ledger
from .models import Decision
from .risk import DayState

NETWORK_FEE_NOTE = "base fee + jito tip per simulated transaction"


def parse_curve(data: bytes) -> tuple[float, float, float, bool] | None:
    """(virtual_sol SOL, virtual_token whole, real_sol SOL, complete)."""
    try:
        vt, vs, _rt, rs, _supply, complete = struct.unpack_from("<QQQQQB", data, 8)
    except Exception:
        return None
    if vt <= 0:
        return None
    return vs / LAMPORTS, vt / TOKEN_DECIMALS, rs / LAMPORTS, bool(complete)


def network_fee_sol(cfg: Config) -> float:
    return (cfg.base_fee_lamports + cfg.jito_tip_lamports) / LAMPORTS


def entry_fill(cfg: Config, v_sol: float, v_tok: float, size_sol: float) -> dict:
    """Simulated buy. The pump.fun fee is charged inside the trade (less
    reaches the curve); the portal fee and network fee are wallet-side."""
    curve_in = size_sol * (1.0 - cfg.fee_pumpfun_pct)
    tokens, avg_price = curve_math.buy_quote(v_sol, v_tok, curve_in)
    portal_fee = size_sol * cfg.fee_pumpportal_pct
    net_fee = network_fee_sol(cfg)
    return {"tokens": tokens, "avg_price": avg_price,
            "fee_sol": size_sol * cfg.fee_pumpfun_pct + portal_fee + net_fee,
            "cost_total": size_sol + portal_fee + net_fee}


def exit_fill(cfg: Config, v_sol: float, v_tok: float, tokens: float) -> dict:
    """Simulated sell of `tokens` at the current curve point."""
    gross, avg_price = curve_math.sell_quote(v_sol, v_tok, tokens)
    trade_fee = gross * cfg.fee_pumpfun_pct
    portal_fee = gross * cfg.fee_pumpportal_pct
    net_fee = network_fee_sol(cfg)
    return {"sol_net": gross - trade_fee - portal_fee - net_fee,
            "avg_price": avg_price,
            "fee_sol": trade_fee + portal_fee + net_fee}


@dataclass
class Position:
    mint: str
    entry_ts: float
    entry_price: float
    tokens_initial: float
    tokens_held: float
    cost_total_sol: float
    realised_sol: float = 0.0
    tp_done: bool = False
    peak_price: float = 0.0
    last_price: float | None = None
    status: str = "open"
    exit_ts: float | None = None
    exit_reason: str | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=120))

    def to_row(self) -> dict:
        return {"mint": self.mint, "entry_ts": self.entry_ts,
                "entry_price": self.entry_price,
                "tokens_initial": self.tokens_initial,
                "tokens_held": self.tokens_held,
                "cost_total_sol": self.cost_total_sol,
                "realised_sol": self.realised_sol,
                "tp_done": self.tp_done, "peak_price": self.peak_price,
                "last_price": self.last_price, "status": self.status,
                "exit_ts": self.exit_ts, "exit_reason": self.exit_reason}

    @classmethod
    def from_row(cls, row: dict) -> "Position":
        return cls(mint=row["mint"], entry_ts=row["entry_ts"],
                   entry_price=row["entry_price"],
                   tokens_initial=row["tokens_initial"],
                   tokens_held=row["tokens_held"],
                   cost_total_sol=row["cost_total_sol"],
                   realised_sol=row["realised_sol"],
                   tp_done=bool(row["tp_done"]),
                   peak_price=row["peak_price"],
                   last_price=row["last_price"], status=row["status"],
                   exit_ts=row["exit_ts"], exit_reason=row["exit_reason"])


def evaluate_exit(pos: Position, price: float, real_sol: float,
                  ts: float, cfg: Config) -> tuple[str, float, str] | None:
    """Pure S10 exit evaluation. Returns (kind, fraction_of_held, reason)
    with kind 'full' or 'partial', or None to keep holding.

    Priority: rug-exit > stop-loss > take-profit stage 1 (partial) >
    trailing stop > decay exit > max hold."""
    held_for = ts - pos.entry_ts
    # Rug-exit: real SOL in the curve drained fast while we hold.
    window = [(t, s) for (t, s) in pos.samples
              if ts - t <= cfg.pool_drain_window_seconds]
    if window:
        peak_sol = max(s for _, s in window)
        if peak_sol > 0 and real_sol < peak_sol * (1.0 - cfg.pool_drain_rug_pct):
            return "full", 1.0, "rug_exit"
    if price <= pos.entry_price * (1.0 - cfg.stop_loss_pct):
        return "full", 1.0, "stop_loss"
    if not pos.tp_done and price >= pos.entry_price * (1.0 + cfg.take_profit_pct):
        return "partial", cfg.take_profit_fraction, "take_profit_1"
    if pos.tp_done and price <= pos.peak_price * (1.0 - cfg.trailing_pct):
        return "full", 1.0, "trailing_stop"
    if held_for >= cfg.decay_exit_seconds and \
            pos.peak_price < pos.entry_price * 1.20:
        return "full", 1.0, "decay_exit"
    if held_for >= cfg.max_hold_seconds:
        return "full", 1.0, "max_hold"
    return None


class Executor:
    """Paper position book. Thread-safe; exits run on price updates."""

    def __init__(self, cfg: Config, ledger: Ledger, rpc, budget=None):
        self.cfg = cfg
        self.ledger = ledger
        self.rpc = rpc
        self.budget = budget
        self.lock = threading.Lock()
        self.positions: dict[str, Position] = {}
        for row in ledger.load_open_positions():
            pos = Position.from_row(row)
            self.positions[pos.mint] = pos
        self._last_peak_save = 0.0

    # ---------- entry
    def enter(self, dec: Decision, snapshot_price: float | None) -> bool:
        """Attempt a paper entry for an accepted decision. The fresh
        curve read is the checker: price must not have collapsed (rug
        between analysis and entry) or run away (>2x) since analysis."""
        cfg = self.cfg
        size = _parse_size(dec.detail)
        if size is None or size < cfg.min_position_sol:
            return False
        if self.budget is not None and not self.budget.allow("rpc", 1):
            self.ledger.event(time.time(), "paper_skip", dec.mint,
                              {"reason": "rpc_budget_exhausted"})
            return False
        try:
            got = self.rpc.account_data(bonding_curve_address(dec.mint))
        except Exception:
            got = None
        if self.budget is not None:
            self.budget.spend("rpc", 1)
        if got is None:
            return False
        parsed = parse_curve(got[0])
        if parsed is None:
            return False
        v_sol, v_tok, real_sol, complete = parsed
        if complete:
            return False
        price = v_sol / v_tok
        if snapshot_price:
            if price < snapshot_price * 0.5 or price > snapshot_price * 2.0:
                self.ledger.event(time.time(), "paper_skip", dec.mint,
                                  {"reason": "checker_price_moved",
                                   "snapshot": snapshot_price, "fresh": price})
                return False
        fill = entry_fill(cfg, v_sol, v_tok, size)
        if fill["tokens"] <= 0:
            return False
        now = time.time()
        pos = Position(mint=dec.mint, entry_ts=now, entry_price=fill["avg_price"],
                       tokens_initial=fill["tokens"],
                       tokens_held=fill["tokens"],
                       cost_total_sol=fill["cost_total"],
                       peak_price=fill["avg_price"], last_price=fill["avg_price"])
        pos.samples.append((now, real_sol))
        with self.lock:
            if dec.mint in self.positions:
                return False
            self.positions[dec.mint] = pos
        self.ledger.save_position(pos.to_row())
        self.ledger.add_fill(now, dec.mint, "entry", None, fill["avg_price"],
                             fill["tokens"], size, fill["fee_sol"], None)
        self.ledger.event(now, "paper_entry", dec.mint,
                          {"size_sol": size, "price": fill["avg_price"],
                           "tokens": fill["tokens"], "score": dec.score})
        return True

    # ---------- price updates -> exits
    def on_price(self, mint: str, v_sol: float, v_tok: float,
                 real_sol: float, ts: float) -> None:
        with self.lock:
            pos = self.positions.get(mint)
        if pos is None or pos.status != "open":
            return
        price = v_sol / v_tok
        pos.last_price = price
        if price > pos.peak_price:
            pos.peak_price = price
            if ts - self._last_peak_save > 5:
                self._last_peak_save = ts
                self.ledger.save_position(pos.to_row())
        pos.samples.append((ts, real_sol))
        action = evaluate_exit(pos, price, real_sol, ts, self.cfg)
        if action is not None:
            self._execute_exit(pos, action, v_sol, v_tok, ts)

    def _execute_exit(self, pos: Position, action, v_sol: float,
                      v_tok: float, ts: float) -> None:
        kind, fraction, reason = action
        tokens = pos.tokens_held * fraction
        if tokens <= 0:
            return
        fill = exit_fill(self.cfg, v_sol, v_tok, tokens)
        basis = pos.cost_total_sol * (tokens / pos.tokens_initial)
        pnl = fill["sol_net"] - basis
        pos.tokens_held -= tokens
        pos.realised_sol += pnl
        if kind == "partial":
            pos.tp_done = True
            self.ledger.add_fill(ts, pos.mint, "exit_partial", reason,
                                 fill["avg_price"], tokens, fill["sol_net"],
                                 fill["fee_sol"], pnl)
            self.ledger.event(ts, "paper_exit_partial", pos.mint,
                              {"reason": reason, "pnl_sol": pnl,
                               "price": fill["avg_price"]})
            self.ledger.save_position(pos.to_row())
            return
        pos.status = "closed"
        pos.exit_ts = ts
        pos.exit_reason = reason
        self.ledger.add_fill(ts, pos.mint, "exit_final", reason,
                             fill["avg_price"], tokens, fill["sol_net"],
                             fill["fee_sol"], pnl)
        self.ledger.event(ts, "paper_exit", pos.mint,
                          {"reason": reason, "pnl_sol": pnl,
                           "total_realised_sol": pos.realised_sol,
                           "held_seconds": ts - pos.entry_ts})
        self.ledger.save_position(pos.to_row())
        with self.lock:
            self.positions.pop(pos.mint, None)

    # ---------- state for risk gate + heartbeat
    def day_state(self, now: float) -> DayState:
        day_start = now - (now % 86400.0)  # UTC day, matches RequestBudget
        agg = self.ledger.paper_day_aggregates(day_start)
        with self.lock:
            open_count = len(self.positions)
        return DayState(realised_pnl_sol=agg["realised_today_sol"],
                        spent_sol=agg["spent_sol"],
                        trades_today=agg["entries"],
                        open_positions=open_count)

    def open_mints(self) -> list[str]:
        with self.lock:
            return list(self.positions.keys())

    def summary(self, now: float) -> dict:
        day_start = now - (now % 86400.0)
        agg = self.ledger.paper_day_aggregates(day_start)
        with self.lock:
            open_positions = len(self.positions)
        return {"open_positions": open_positions,
                "entries_today": agg["entries"],
                "realised_today_sol": round(agg["realised_today_sol"], 5),
                "realised_total_sol": round(agg["realised_total_sol"], 5),
                "paper_equity_sol": round(
                    self.cfg.paper_capital_sol + agg["realised_total_sol"], 5)}


def _parse_size(detail: str) -> float | None:
    for part in (detail or "").split():
        if part.startswith("size_sol="):
            try:
                return float(part.split("=", 1)[1])
            except ValueError:
                return None
    return None
