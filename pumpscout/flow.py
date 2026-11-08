"""S5 holder distribution and trade-flow heuristics (T-HOLDER, T-FLOW).

Pure functions over the Analysis snapshot: concentration vetoes plus the
measurable components (diversity, bundle, sniper, coordination, wash)
that feed the score. Heuristics catch known patterns only - that is why
the design keeps the S8 checker, capital brakes and rug-exit behind them.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .models import Analysis

SNIPER_WINDOW_SECONDS = 15.0
CLUSTER_WINDOW_SECONDS = 5.0
CLUSTER_SIZE_TOLERANCE = 0.05


@dataclass(frozen=True)
class FlowReport:
    veto: str | None
    reason_code: str | None           # soft rejection when not a veto
    top10_pct: float | None
    creator_pct: float | None
    top5_pct: float | None
    curve_health: float | None        # 1 - insider (top5) share
    wallet_diversity: float | None
    unique_buyers: int | None
    bundle_pct: float | None
    sniper_pct: float | None
    coordinated: bool
    organic_share: float | None


def evaluate_flow(a: Analysis, cfg: Config, creator: str,
                  supply_tokens: float | None) -> FlowReport:
    if not a.holders_read or supply_tokens is None or supply_tokens <= 0:
        return FlowReport("safety_blind", None, None, None, None, None,
                          None, None, None, None, False, None)
    holders = a.top_holders
    top10 = sum(h.pct for h in holders[:10])
    top5 = sum(h.pct for h in holders[:5])
    creator_pct = next((h.pct for h in holders if h.wallet == creator), 0.0)

    # Absolute vetoes first (PumpGrok analyzer rules).
    if creator_pct > 0.25 or top5 > 0.80:
        return FlowReport("safety_veto", None, top10, creator_pct, top5,
                          1.0 - top5, None, None, None, None, False, None)
    if top10 > cfg.top10_fail_pct:
        return FlowReport(None, "high_risk", top10, creator_pct, top5,
                          1.0 - top5, None, None, None, None, False, None)

    diversity = unique = organic = bundle = sniper = None
    coordinated = False
    if a.trades_read and a.trades:
        trades = a.trades
        buyers = [t for t in trades if t.is_buy]
        unique_wallets = {t.wallet for t in trades}
        unique = len({t.wallet for t in buyers})
        diversity = len(unique_wallets) / len(trades)
        both_sides = {t.wallet for t in buyers} & {t.wallet for t in trades if not t.is_buy}
        organic = 1.0 - (len(both_sides) / max(1, len(unique_wallets)))
        if supply_tokens:
            first_slot = min((t.slot for t in trades), default=0)
            bundle_tokens = sum(t.token_amount for t in buyers if t.slot == first_slot)
            bundle = bundle_tokens / supply_tokens
            t0 = min(t.ts for t in trades)
            sniper_tokens = sum(t.token_amount for t in buyers
                                if t.ts - t0 <= SNIPER_WINDOW_SECONDS)
            sniper = sniper_tokens / supply_tokens
        coordinated = _has_cluster(buyers)

    return FlowReport(None, None, top10, creator_pct, top5, 1.0 - top5,
                      diversity, unique, bundle, sniper, coordinated, organic)


def _has_cluster(buys) -> bool:
    """≥3 distinct wallets buying near-identical sizes within 5 seconds."""
    ordered = sorted(buys, key=lambda t: t.ts)
    for i, t in enumerate(ordered):
        group = [t]
        for u in ordered[i + 1:]:
            if u.ts - t.ts > CLUSTER_WINDOW_SECONDS:
                break
            if t.sol_amount > 0 and abs(u.sol_amount - t.sol_amount) / t.sol_amount <= CLUSTER_SIZE_TOLERANCE:
                group.append(u)
        if len({g.wallet for g in group}) >= 3:
            return True
    return False


def flow_rejection(rep: FlowReport, cfg: Config) -> str | None:
    """Soft gates applied after vetoes, in cheap-to-expensive order."""
    if rep.unique_buyers is not None and rep.unique_buyers < cfg.min_unique_buyers:
        return "few_buyers"
    if rep.wallet_diversity is not None and rep.wallet_diversity < cfg.min_wallet_diversity:
        return "low_diversity"
    if rep.organic_share is not None and rep.organic_share < cfg.min_organic_share:
        return "low_organic"
    if rep.bundle_pct is not None and rep.bundle_pct > cfg.bundle_reject_above:
        return "high_risk"
    return None
