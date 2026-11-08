"""S8 weighted scoring - only measurable components, vetoes already applied.

Weights are config (design section S8): risk_inverse 0.35, liquidity /
curve health 0.20, wallet diversity 0.20, potential proxies 0.15,
social presence 0.10. A missing component makes the token unscorable
(fail-closed), it is never silently re-weighted.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class ScoreReport:
    total: float | None
    breakdown: tuple[tuple[str, float], ...]
    missing: tuple[str, ...]


def compute_score(cfg: Config, *, risk_score: float | None,
                  liquidity_health: float | None,
                  wallet_diversity: float | None,
                  potential: float | None,
                  social: float | None) -> ScoreReport:
    comps = {
        "risk_inverse": (cfg.weights_risk_inverse,
                         None if risk_score is None else 1.0 - risk_score),
        "liquidity_curve": (cfg.weights_liquidity_curve, liquidity_health),
        "wallet_diversity": (cfg.weights_wallet_diversity, wallet_diversity),
        "potential": (cfg.weights_potential, potential),
        "social": (cfg.weights_social, social),
    }
    missing = tuple(k for k, (_, v) in comps.items() if v is None)
    if missing:
        return ScoreReport(None, (), missing)
    total = sum(w * v for w, v in comps.values())  # type: ignore[operator]
    return ScoreReport(total, tuple((k, v) for k, (_, v) in comps.items()), ())
