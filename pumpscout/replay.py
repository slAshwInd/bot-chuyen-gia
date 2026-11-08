"""Replay engine (design section 8): re-run stages S3-S8 offline over
recorded Analysis snapshots with a candidate config, and compare the
decision funnel against the fate labels in the ledger.

The executor is always absent here - replay can only produce shadow
decisions, by construction. Datasets are the recorder's own output, so
results carry no survivorship bias from purchased history.

Run:  python -m pumpscout.replay --data-dir /path/runtime [--config new.toml]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from collections import Counter
from pathlib import Path

from .config import load_config
from .flow import evaluate_flow, flow_rejection
from .ledger import Ledger
from .liquidity import evaluate_liquidity
from .models import Analysis, HolderSlice, Trade
from .pipeline import _flow_dynamics
from .potential import evaluate_potential
from .safety import evaluate_safety
from . import scoring as scoring_mod


def _to_analysis(snap: dict) -> Analysis:
    snap = dict(snap)
    snap["top_holders"] = tuple(HolderSlice(**h) for h in snap.get("top_holders", []))
    snap["trades"] = tuple(Trade(**t) for t in snap.get("trades", []))
    snap["health"] = tuple(snap.get("health", []))
    known = {f.name for f in dataclasses.fields(Analysis)}
    return Analysis(**{k: v for k, v in snap.items() if k in known})


def replay_decision(cfg, a: Analysis, creator: str) -> str:
    safety = evaluate_safety(a, cfg)
    if safety.veto:
        return safety.veto
    if safety.blind:
        return "safety_blind"
    if a.rugcheck_score is not None and a.rugcheck_score > cfg.provider_risk_reject:
        return "high_risk"
    liq = evaluate_liquidity(a, cfg, cfg.max_position_sol)
    if not liq.ok:
        return liq.reason_code or "data_unavailable"
    flow = evaluate_flow(a, cfg, creator, a.supply_tokens)
    if flow.veto:
        return flow.veto
    if flow.reason_code:
        return flow.reason_code
    soft = flow_rejection(flow, cfg)
    if soft:
        return soft
    growth, accel = _flow_dynamics(a)
    pot = evaluate_potential(a, growth, accel)
    liq_health = None
    if liq.multiple is not None:
        liq_health = min(1.0, liq.multiple / cfg.liquidity_multiple_recommended)
    if flow.curve_health is not None and liq_health is not None:
        liq_health = (liq_health + flow.curve_health) / 2.0
    risk_score = safety.risk_score
    if flow.top10_pct is not None:
        risk_score = max(risk_score,
                         min(1.0, flow.top10_pct / cfg.top10_fail_pct))
    score = scoring_mod.compute_score(
        cfg, risk_score=risk_score, liquidity_health=liq_health,
        wallet_diversity=flow.wallet_diversity,
        potential=pot.score, social=pot.social_score)
    if score.total is None:
        return "data_unavailable"
    if pot.score is not None and pot.score < cfg.min_potential_score:
        return "low_potential"
    if score.total < cfg.min_total_score:
        return "low_score"
    return "shadow_would_buy"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    cfg = load_config(args.config, data_dir)
    ledger = Ledger(data_dir / "ledger.db")
    creators = {r[0]: r[1] for r in ledger.db.execute(
        "SELECT mint, creator FROM tokens").fetchall()}
    funnel: Counter = Counter()
    rows = ledger.db.execute("SELECT mint, snapshot FROM analyses").fetchall()
    for mint, snap_json in rows:
        a = _to_analysis(json.loads(snap_json))
        funnel[replay_decision(cfg, a, creators.get(mint, ""))] += 1
    ledger.close()
    print(f"replayed analyses: {sum(funnel.values())}")
    for code, n in funnel.most_common():
        print(f"  {code}: {n}")


if __name__ == "__main__":
    main()
