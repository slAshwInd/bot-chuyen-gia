"""Risk gate (task T-RISK): the five brakes plus score-based sizing.

Unlike TrendMR (alerts only), a hit daily-loss limit here is a HARD
block on new entries - a memecoin position has no exchange-side stop
protecting it, so capital brakes are the last protection layer. Exits
are never blocked by this gate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class DayState:
    realised_pnl_sol: float = 0.0     # negative = loss
    spent_sol: float = 0.0            # gross notional opened today
    trades_today: int = 0
    open_positions: int = 0


@dataclass(frozen=True)
class RiskVerdict:
    allowed: bool
    reason_code: str | None
    size_sol: float


def can_trade(cfg: Config, day: DayState, score: float, *,
              kill_flag: bool, regime_is_open: bool) -> RiskVerdict:
    if kill_flag:
        return RiskVerdict(False, "risk_limit", 0.0)
    if -day.realised_pnl_sol >= cfg.daily_loss_limit_sol:
        return RiskVerdict(False, "risk_limit", 0.0)
    if day.trades_today >= cfg.max_trades_per_day:
        return RiskVerdict(False, "risk_limit", 0.0)
    if day.open_positions >= cfg.max_open_positions:
        return RiskVerdict(False, "risk_limit", 0.0)
    if not regime_is_open:
        return RiskVerdict(False, "regime_closed", 0.0)
    remaining_budget = max(0.0, cfg.daily_budget_sol - day.spent_sol)
    size = min(cfg.max_position_sol * score, remaining_budget * 0.30)
    if size < cfg.min_position_sol:
        return RiskVerdict(False, "risk_limit", 0.0)
    return RiskVerdict(True, None, size)
