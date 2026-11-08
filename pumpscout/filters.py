"""S1 fast filter - the cheapest checks first (principle P1).

Runs on stream data only: no RPC, no API calls. A token that dies here
costs the system nothing. Returns a reason_code or None (pass).
"""
from __future__ import annotations

from .config import Config
from .models import NewToken


def fast_filter(tok: NewToken, now: float, cfg: Config,
                blacklisted_creators: set[str],
                creators_with_open_position: set[str]) -> str | None:
    if not tok.name or not tok.symbol or not tok.uri:
        return "no_metadata"
    if tok.creator in blacklisted_creators:
        return "creator_blacklisted"
    if tok.creator in creators_with_open_position:
        return "creator_position_open"
    age = now - (tok.created_ts or tok.seen_ts)
    if age < cfg.min_age_seconds:
        return "too_young"
    if tok.curve_pct is not None and tok.curve_pct >= cfg.max_curve_pct:
        return "curve_too_full"
    return None
