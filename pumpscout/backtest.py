"""Historical evaluation harness (T-DATA): replays recorded decisions
against the token's real subsequent trade path.

There is no vendor historical dataset for pump.fun launches, so the
harness reconstructs each decided token's price path from its actual
on-chain trades (Helius enhanced transactions on the bonding-curve
account, paginated and cached), then simulates the paper fill model
and the S10 exit rules on that path. Entry uses the first real trade
at or after decision_time + 2s - the same information the live paper
executor would have had, no look-ahead in the signal itself.

Known approximations, stated rather than hidden:
- Fills are priced from real trade prices, not the curve integral
  (the integral needs reserve history, which trades do not carry).
  The full fee stack (pump.fun 1%, portal 0.5%, network per tx) IS
  applied identically to the live paper executor.
- Rug-exit (real-SOL drain) cannot be reconstructed from trades and
  is inactive here; live paper keeps it. Backtest results are
  therefore mildly optimistic on rug paths.

Usage:
  python -m pumpscout.backtest --config ~/pumpscout-runtime/pumpscout.toml \
      --ledger ~/pumpscout-runtime/ledger.db [--max-tokens 30]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import requests

from .config import load_config
from .enrichment import bonding_curve_address
from .executor import Position, evaluate_exit, network_fee_sol
from .surrogate import resolve_rpc_url

LAMPORTS = 1_000_000_000


def fetch_path(mint: str, api_key_query: str, cache_dir: Path,
               max_pages: int = 5) -> list[dict]:
    """Trade price points for a mint, ascending by time, cached on disk."""
    cache = cache_dir / f"{mint}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    curve = bonding_curve_address(mint)
    points: list[dict] = []
    before = None
    session = requests.Session()
    for _ in range(max_pages):
        url = (f"https://api.helius.xyz/v0/addresses/{curve}/transactions"
               f"?{api_key_query}&limit=100")
        if before:
            url += f"&before={before}"
        r = session.get(url, timeout=20)
        r.raise_for_status()
        txs = r.json()
        if not txs:
            break
        for tx in txs:
            changes = {c.get("account"): (c.get("nativeBalanceChange") or 0)
                       for c in tx.get("accountData", [])}
            for tr in tx.get("tokenTransfers", []):
                if tr.get("mint") != mint:
                    continue
                frm, to = tr.get("fromUserAccount"), tr.get("toUserAccount")
                if to == curve and frm and frm != curve:
                    wallet = frm
                elif frm == curve and to and to != curve:
                    wallet = to
                else:
                    continue
                tokens = float(tr.get("tokenAmount") or 0)
                sol = abs(changes.get(wallet, 0)) / LAMPORTS
                if tokens > 0 and sol > 0:
                    points.append({"ts": float(tx.get("timestamp") or 0),
                                   "price": sol / tokens, "sol": sol})
        before = txs[-1].get("signature")
        if len(txs) < 100:
            break
    points.sort(key=lambda p: p["ts"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(points), encoding="utf-8")
    return points


def simulate(decision_ts: float, size_sol: float, path: list[dict],
             cfg) -> dict | None:
    """Simulate one paper position on a reconstructed price path."""
    entry_idx = None
    for i, p in enumerate(path):
        if p["ts"] >= decision_ts + 2.0:
            entry_idx = i
            break
    if entry_idx is None:
        return None
    entry_price = path[entry_idx]["price"]
    net_fee = network_fee_sol(cfg)
    curve_in = size_sol * (1.0 - cfg.fee_pumpfun_pct)
    tokens = curve_in / entry_price
    cost_total = size_sol + size_sol * cfg.fee_pumpportal_pct + net_fee
    pos = Position(mint="bt", entry_ts=path[entry_idx]["ts"],
                   entry_price=entry_price, tokens_initial=tokens,
                   tokens_held=tokens, cost_total_sol=cost_total,
                   peak_price=entry_price)
    realised = 0.0
    exit_reason = "open_at_data_end"
    exit_ts = path[-1]["ts"]
    for p in path[entry_idx + 1:]:
        price = p["price"]
        pos.peak_price = max(pos.peak_price, price)
        action = evaluate_exit(pos, price, real_sol=0.0, ts=p["ts"], cfg=cfg)
        if action is None:
            continue
        kind, fraction, reason = action
        sell_tokens = pos.tokens_held * fraction
        gross = sell_tokens * price
        net = gross * (1.0 - cfg.fee_pumpfun_pct - cfg.fee_pumpportal_pct) \
            - net_fee
        basis = cost_total * (sell_tokens / tokens)
        realised += net - basis
        pos.tokens_held -= sell_tokens
        exit_ts = p["ts"]
        if kind == "partial":
            pos.tp_done = True
            continue
        exit_reason = reason
        break
    closed = exit_reason != "open_at_data_end"
    return {"entry_ts": pos.entry_ts, "exit_ts": exit_ts,
            "entry_price": entry_price, "pnl_sol": realised if closed else None,
            "exit_reason": exit_reason, "closed": closed}


def run(ledger_path: Path, cfg, api_key_query: str, cache_dir: Path,
        max_tokens: int) -> dict:
    db = sqlite3.connect(str(ledger_path))
    funnel = db.execute(
        "SELECT reason_code, COUNT(*) FROM decisions GROUP BY reason_code"
        " ORDER BY COUNT(*) DESC").fetchall()
    accepted = db.execute(
        "SELECT mint, ts, detail FROM decisions WHERE accepted = 1"
        " ORDER BY ts").fetchall()
    fates = db.execute(
        "SELECT label, COUNT(*) FROM fate_labels GROUP BY label").fetchall()
    trades = []
    skipped_no_path = 0
    for mint, ts, detail in accepted[:max_tokens]:
        size = cfg.max_position_sol
        for part in (detail or "").split():
            if part.startswith("size_sol="):
                try:
                    size = float(part.split("=", 1)[1])
                except ValueError:
                    pass
        path = fetch_path(mint, api_key_query, cache_dir)
        got = simulate(ts, size, path, cfg)
        if got is None:
            skipped_no_path += 1
            continue
        got["mint"] = mint
        trades.append(got)
    closed = [t for t in trades if t["closed"]]
    wins = [t for t in closed if (t["pnl_sol"] or 0) > 0]
    gross_win = sum(t["pnl_sol"] for t in wins)
    gross_loss = -sum(t["pnl_sol"] for t in closed if (t["pnl_sol"] or 0) <= 0)
    equity = cfg.paper_capital_sol
    peak = equity
    max_dd = 0.0
    for t in sorted(closed, key=lambda x: x["exit_ts"]):
        equity += t["pnl_sol"] or 0.0
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0 if peak else 0.0)
    reasons: dict[str, int] = {}
    for t in closed:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return {"funnel": funnel, "fate_labels": fates,
            "accepted_total": len(accepted),
            "simulated": len(trades), "skipped_no_path": skipped_no_path,
            "closed": len(closed),
            "winrate": (len(wins) / len(closed)) if closed else None,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
            "net_pnl_sol": sum(t["pnl_sol"] or 0.0 for t in closed),
            "max_drawdown_pct": max_dd * 100.0,
            "exit_reasons": reasons, "trades": trades}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--cache-dir", default="/home/hatch/pumpscout-runtime/bt-cache")
    ap.add_argument("--max-tokens", type=int, default=30)
    args = ap.parse_args()
    cfg = load_config(args.config, Path(args.ledger).parent)
    resolved, status = resolve_rpc_url(cfg.rpc_url)
    if status != "surrogate":
        raise SystemExit("Helius credential could not be resolved")
    from urllib.parse import parse_qs, urlparse
    api_key_query = "api-key=" + parse_qs(urlparse(resolved).query)["api-key"][0]
    report = run(Path(args.ledger), cfg, api_key_query,
                 Path(args.cache_dir), args.max_tokens)
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
