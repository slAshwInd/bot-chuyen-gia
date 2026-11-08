"""S7 potential proxies and market-regime gate (T-POT, T-TIMING).

The reference system let an LLM judge "narrative". PumpScout is no-AI by
design, so potential is only what can be counted: social presence,
growth velocity of unique buyers, volume acceleration. The component
carries a deliberately small weight (0.15) and can never override a veto.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Analysis


@dataclass(frozen=True)
class PotentialReport:
    score: float | None               # 0..1, None = blind
    social_score: float | None        # 0..1


def evaluate_potential(a: Analysis, buyer_growth_per_min: float | None,
                       volume_accel: float | None) -> PotentialReport:
    if a.has_socials is None:
        return PotentialReport(None, None)
    social = 0.0
    if a.has_socials:
        social += 0.6
    if a.reply_count:
        social += min(0.4, a.reply_count / 50.0)
    parts: list[float] = [social]
    if buyer_growth_per_min is not None:
        parts.append(min(1.0, buyer_growth_per_min / 20.0))
    if volume_accel is not None:
        parts.append(max(0.0, min(1.0, volume_accel)))
    return PotentialReport(sum(parts) / len(parts), social)


@dataclass(frozen=True)
class RegimeSnapshot:
    ts: float
    score: float                      # 0..1 from stream statistics
    launches_per_hour: float
    graduations_per_hour: float


def regime_open(snap: RegimeSnapshot | None, now: float, stale_seconds: float,
                min_score: float = 0.25) -> bool:
    """Fail-closed: no snapshot, or a stale one, means the gate is closed."""
    if snap is None:
        return False
    if now - snap.ts > stale_seconds:
        return False
    return snap.score >= min_score
