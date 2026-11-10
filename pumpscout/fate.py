"""Fate labelling - the ground truth that shadow mode is measured against.

For every decided token, at +1h / +6h / +24h, re-read the bonding curve
account directly on-chain (no dependence on the pump.fun frontend API)
and label it: graduated (curve complete), rug (price collapsed >=95%
vs the decision snapshot), dead (>=80% collapse), or alive.
False-safety = WOULD_BUY tokens labelled rug/dead; false-negative =
rejected tokens that graduated.
"""
from __future__ import annotations

import struct
import time

from .enrichment import LAMPORTS, TOKEN_DECIMALS, Rpc, bonding_curve_address
from .ledger import Ledger

HORIZONS = (3600.0, 6 * 3600.0, 24 * 3600.0)


def read_curve_price(rpc: Rpc, mint: str) -> tuple[float, bool] | None:
    """(spot price in SOL, graduated) from the curve account, or None."""
    try:
        got = rpc.account_data(bonding_curve_address(mint))
        if got is None:
            return None
        data, _owner = got
        vt, vs, _rt, _rs, _supply, complete = struct.unpack_from("<QQQQQB", data, 8)
        if vt <= 0:
            return None
        return (vs / LAMPORTS) / (vt / TOKEN_DECIMALS), bool(complete)
    except Exception:
        return None


def label_for(mint: str, ledger: Ledger, rpc: Rpc) -> tuple[str, float | None, str] | None:
    got = read_curve_price(rpc, mint)
    if got is None:
        return None
    now_price, complete = got
    snap = ledger.latest_analysis(mint) or {}
    then_price = None
    if snap.get("virtual_sol") and snap.get("virtual_token"):
        then_price = snap["virtual_sol"] / snap["virtual_token"]
    change = (now_price / then_price - 1.0) if then_price else None
    if complete:
        return "graduated", change, "bonding curve complete"
    if change is not None and change <= -0.95:
        return "rug", change, "price collapsed >=95%"
    if change is not None and change <= -0.80:
        return "dead", change, "price collapsed >=80%"
    return "alive", change, ""


def run_fate_pass(ledger: Ledger, rpc: Rpc, now: float | None = None,
                  budget=None, per_horizon_cap: int = 150,
                  abort_after_failures: int = 5,
                  defer_after: int = 3) -> int:
    """One labelling pass. Each horizon gets its own cap of
    per_horizon_cap labels, oldest decisions first, so a swollen 1h
    backlog can no longer starve the 6h/24h horizons of their turn
    (the pre-0.3.2 pass shared one global cap in 1h -> 6h -> 24h
    order and never reached the later horizons).

    Call-mechanism guards retained: every curve read is charged to
    the shared rpc budget, and the pass aborts after
    abort_after_failures consecutive unreadable curves - during an
    outage an unbounded pass would churn the whole backlog (thousands
    of doomed calls) every cycle. A mint whose read fails has its
    consecutive-failure count recorded; at defer_after failures it
    drops to the low-priority tail of the queue (see
    Ledger.tokens_needing_fate) instead of being retried first
    forever, and any successful read clears the count.
    """
    now = now or time.time()
    labelled = 0
    failures = 0
    migrated = {r[0] for r in ledger.db.execute(
        "SELECT DISTINCT mint FROM events WHERE kind = 'migration'").fetchall()}
    for horizon in HORIZONS:
        tag = f"{int(horizon // 3600)}h"
        labelled_here = 0
        for mint in ledger.tokens_needing_fate(horizon, now,
                                               defer_after=defer_after):
            if labelled_here >= per_horizon_cap:
                break
            if mint in migrated:
                # The discovery stream saw the migration itself; the curve
                # account may already be closed, so trust the stream event.
                ledger.add_fate_label(mint, tag, now, "graduated", None,
                                      "migration event in stream")
                ledger.clear_fate_failure(mint, tag)
                labelled += 1
                labelled_here += 1
                continue
            if budget is not None:
                if not budget.allow("rpc", 1, now):
                    return labelled
                budget.spend("rpc", 1, now)
            got = label_for(mint, ledger, rpc)
            if got is None:
                ledger.record_fate_failure(mint, tag, now)
                failures += 1
                if failures >= abort_after_failures:
                    return labelled
                continue
            failures = 0
            ledger.clear_fate_failure(mint, tag)
            label, change, detail = got
            ledger.add_fate_label(mint, tag, now, label, change, detail)
            labelled += 1
            labelled_here += 1
    return labelled
