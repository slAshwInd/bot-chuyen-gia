"""Shadow pipeline S0-S8: the P1 heart of PumpScout.

Every token flows through the stages in cheap-to-expensive order and
produces exactly one Decision with a fixed reason_code, stored in the
ledger and the recorder. In shadow mode an "accepted" decision means
WOULD_BUY - nothing is signed, sent or simulated as an order.

Quota discipline (provider budget ~1M requests / 30 days):
- S1 fast filter runs on stream data only: 0 RPC requests.
- Core enrichment is ONE getMultipleAccounts request (mint + curve).
- The holder read (+1 request) happens only after the cheap contract
  and liquidity vetoes pass - most tokens never cost it.
- A persisted daily RequestBudget and an hourly analysis pace cap how
  fast tokens may consume requests at all; both fail closed.
"""
from __future__ import annotations

import dataclasses
import time
from collections import deque
from dataclasses import asdict

from . import scoring as scoring_mod
from .budget import RequestBudget
from .config import Config
from .creator import CreatorStore
from .discovery import SeenSet
from .filters import fast_filter
from .flow import evaluate_flow, flow_rejection
from .ledger import Ledger
from .liquidity import evaluate_liquidity
from .models import Decision, NewToken
from .potential import evaluate_potential
from .recorder import Recorder
from .risk import DayState, can_trade
from .safety import evaluate_safety

CORE_RPC_COST = 1      # getMultipleAccounts(mint, curve)
HOLDERS_RPC_COST = 1   # getTokenLargestAccounts


class ShadowPipeline:
    def __init__(self, cfg: Config, ledger: Ledger, recorder: Recorder,
                 creators: CreatorStore, enricher, budget: RequestBudget):
        self.cfg = cfg
        self.ledger = ledger
        self.recorder = recorder
        self.creators = creators
        self.enricher = enricher
        self.budget = budget
        self.seen = SeenSet(cfg.seen_max)
        self.buffer: dict[str, NewToken] = {}
        self._analysis_times: deque[float] = deque()

    # ---- S0 intake
    def on_token(self, tok: NewToken) -> None:
        if not self.seen.add(tok.mint):
            return
        self.recorder.record("new_token", tok.seen_ts, asdict(tok))
        self.ledger.add_token(tok.mint, tok.creator, tok.name, tok.symbol,
                              tok.uri, tok.created_ts, tok.seen_ts)
        self.creators.record(tok.creator, launch=True)
        if len(self.buffer) >= self.cfg.buffer_max:
            self._decide(tok, "S0", False, "buffer_overflow", None, (), "")
            return
        self.buffer[tok.mint] = tok

    # ---- ripening sweep: analyse everything old enough, within pace
    def sweep(self, now: float, day: DayState, regime_ok: bool) -> list[Decision]:
        out: list[Decision] = []
        while self._analysis_times and now - self._analysis_times[0] > 3600:
            self._analysis_times.popleft()
        for mint, tok in list(self.buffer.items()):
            if now - tok.seen_ts < self.cfg.min_age_seconds:
                continue
            if now - tok.seen_ts > self.cfg.buffer_ttl_seconds:
                del self.buffer[mint]
                out.append(self._decide(tok, "S0", False, "stale_no_traction",
                                        None, (), ""))
                continue
            if len(self._analysis_times) >= self.cfg.max_analyses_per_hour:
                continue  # pace cap: stay buffered until the window frees up
            if not self.budget.allow("rpc", CORE_RPC_COST, now):
                del self.buffer[mint]
                out.append(self._decide(tok, "S2", False, "data_unavailable",
                                        None, (), "rpc_budget_exhausted"))
                continue
            del self.buffer[mint]
            self._analysis_times.append(now)
            self.budget.spend("rpc", CORE_RPC_COST, now)
            out.append(self._analyse(tok, now, day, regime_ok))
        return out

    # ---- S1-S8
    def _analyse(self, tok: NewToken, now: float, day: DayState,
                 regime_ok: bool) -> Decision:
        cfg = self.cfg
        blacklisted = self.creators.blacklisted_set(cfg.block_creator_after_rugs)
        reason = fast_filter(tok, now, cfg, blacklisted, set())
        if reason:
            return self._decide(tok, "S1", False, reason, None, (), "")

        a = self.enricher.enrich(tok.mint, tok.creator, tok.uri)
        rugs, launches = self.creators.stats(tok.creator)
        a = dataclasses.replace(a, creator_prior_rugs=rugs,
                                creator_prior_launches=launches,
                                creator_blacklisted=tok.creator in blacklisted)

        safety = evaluate_safety(a, cfg)
        if safety.veto:
            return self._finish(tok, a, "S3", False, safety.veto, None, (), "")
        if safety.blind:
            return self._finish(tok, a, "S3", False, "safety_blind", None, (),
                                ",".join(safety.blind))
        if a.rugcheck_score is not None and a.rugcheck_score > cfg.provider_risk_reject:
            return self._finish(tok, a, "S3", False, "high_risk", None, (),
                                "provider risk above threshold")

        liq = evaluate_liquidity(a, cfg, cfg.max_position_sol)
        if not liq.ok:
            return self._finish(tok, a, "S4", False,
                                liq.reason_code or "data_unavailable", None, (), "")

        # Late holder read: only tokens that survived the cheap vetoes
        # are worth this request.
        if not self.budget.allow("rpc", HOLDERS_RPC_COST, now):
            return self._finish(tok, a, "S5", False, "data_unavailable", None,
                                (), "rpc_budget_exhausted")
        self.budget.spend("rpc", HOLDERS_RPC_COST, now)
        a = self.enricher.enrich_holders(a)

        flow = evaluate_flow(a, cfg, tok.creator, a.supply_tokens)
        if flow.veto:
            return self._finish(tok, a, "S5", False, flow.veto, None, (), "")
        if flow.reason_code:
            return self._finish(tok, a, "S5", False, flow.reason_code, None, (), "")
        soft = flow_rejection(flow, cfg)
        if soft:
            return self._finish(tok, a, "S5", False, soft, None, (), "")
        bundle_penalty = bool(flow.bundle_pct and flow.bundle_pct > cfg.bundle_penalty_above)

        growth, accel = _flow_dynamics(a)
        pot = evaluate_potential(a, growth, accel)
        risk_score = safety.risk_score
        if flow.top10_pct is not None:
            risk_score = max(risk_score,
                             min(1.0, flow.top10_pct / cfg.top10_fail_pct))
        if bundle_penalty:
            risk_score = min(1.0, risk_score + 0.15)
        if flow.coordinated:
            risk_score = min(1.0, risk_score + 0.20)
        if flow.sniper_pct:
            risk_score = min(1.0, risk_score + flow.sniper_pct * 0.3)
        liq_health = None
        if liq.multiple is not None:
            liq_health = min(1.0, liq.multiple / cfg.liquidity_multiple_recommended)
        if flow.curve_health is not None and liq_health is not None:
            liq_health = (liq_health + flow.curve_health) / 2.0
        score = scoring_mod.compute_score(
            cfg, risk_score=risk_score, liquidity_health=liq_health,
            wallet_diversity=flow.wallet_diversity,
            potential=pot.score, social=pot.social_score)
        if score.total is None:
            return self._finish(tok, a, "S8", False, "data_unavailable", None, (),
                                "missing:" + ",".join(score.missing))
        if pot.score is not None and pot.score < cfg.min_potential_score:
            return self._finish(tok, a, "S7", False, "low_potential", score.total,
                                score.breakdown, "")
        if score.total < cfg.min_total_score:
            return self._finish(tok, a, "S8", False, "low_score", score.total,
                                score.breakdown, "")

        verdict = can_trade(cfg, day, score.total, kill_flag=False,
                            regime_is_open=regime_ok)
        if not verdict.allowed:
            return self._finish(tok, a, "S8", False,
                                verdict.reason_code or "risk_limit",
                                score.total, score.breakdown, "")
        size = min(verdict.size_sol, liq.adjusted_size_sol)
        if size < cfg.min_position_sol:
            return self._finish(tok, a, "S8", False, "impact_too_high",
                                score.total, score.breakdown, "")
        return self._finish(tok, a, "S8", True, "shadow_would_buy", score.total,
                            score.breakdown, f"size_sol={size:.4f}")

    def _finish(self, tok: NewToken, a, stage: str, accepted: bool, reason: str,
                score: float | None, breakdown: tuple, detail: str) -> Decision:
        now = time.time()
        self.ledger.add_analysis(tok.mint, now, asdict(a))
        self.recorder.record("analysis", now, {"mint": tok.mint, **asdict(a)})
        return self._decide(tok, stage, accepted, reason, score, breakdown, detail)

    def _decide(self, tok: NewToken, stage: str, accepted: bool, reason: str,
                score: float | None, breakdown: tuple, detail: str) -> Decision:
        ts = time.time()
        dec = Decision(tok.mint, ts, stage, accepted, reason, score,
                       breakdown, detail)
        self.ledger.add_decision(tok.mint, ts, stage, accepted, reason,
                                 score, breakdown, detail)
        self.recorder.record("decision", ts, asdict(dec))
        return dec


def _flow_dynamics(a) -> tuple[float | None, float | None]:
    """Buyer growth per minute and volume acceleration from recent trades."""
    if not a.trades_read or len(a.trades) < 4:
        return None, None
    trades = sorted(a.trades, key=lambda t: t.ts)
    span_min = max((trades[-1].ts - trades[0].ts) / 60.0, 0.1)
    buyers = len({t.wallet for t in trades if t.is_buy})
    growth = buyers / span_min
    mid = len(trades) // 2
    first = sum(t.sol_amount for t in trades[:mid])
    second = sum(t.sol_amount for t in trades[mid:])
    accel = (second / first) if first > 0 else None
    return growth, accel
