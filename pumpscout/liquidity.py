"""S4 liquidity and slippage pre-checks (tasks T-LIQ, T-SLIP).

Neither reference source defined thresholds here, so this module owns
them (design section S4): real SOL actually in the curve (virtual
reserves excluded), a liquidity multiple versus order size, and a
pre-trade impact simulation on the curve math.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import curve_math
from .config import Config
from .models import Analysis


@dataclass(frozen=True)
class LiquidityVerdict:
    ok: bool
    reason_code: str | None
    adjusted_size_sol: float
    impact: float | None
    multiple: float | None


def evaluate_liquidity(a: Analysis, cfg: Config, wanted_size_sol: float) -> LiquidityVerdict:
    if a.real_sol is None or a.virtual_sol is None or a.virtual_token is None:
        return LiquidityVerdict(False, "data_unavailable", 0.0, None, None)
    if wanted_size_sol <= 0:
        return LiquidityVerdict(False, "risk_limit", 0.0, None, None)
    multiple = a.real_sol / wanted_size_sol
    if multiple < cfg.liquidity_multiple_min:
        return LiquidityVerdict(False, "impact_too_high", 0.0, None, multiple)
    impact = curve_math.price_impact(a.virtual_sol, a.virtual_token, wanted_size_sol)
    size = wanted_size_sol
    if impact > cfg.impact_reduce_above:
        size = curve_math.size_for_impact(
            a.virtual_sol, a.virtual_token, cfg.impact_reduce_above,
            cfg.min_position_sol, wanted_size_sol)
        impact = curve_math.price_impact(a.virtual_sol, a.virtual_token, size)
        if size <= cfg.min_position_sol and impact > cfg.impact_reject_above:
            return LiquidityVerdict(False, "impact_too_high", 0.0, impact, multiple)
        if impact > cfg.impact_reject_above:
            return LiquidityVerdict(False, "impact_too_high", 0.0, impact, multiple)
    return LiquidityVerdict(True, None, size, impact, multiple)
