"""Central configuration for PumpScout.

One versioned TOML file holds every threshold; no trading threshold may
live as a constant in code (design section 10). Loading is fail-closed:
unknown keys, an unsafe mode/flag combination, or a secret-looking key in
the file all refuse to start.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from . import MODE_LIVE_AUTO, MODE_LIVE_TICKET, MODE_PAPER, MODE_SHADOW

_MODES = (MODE_SHADOW, MODE_PAPER, MODE_LIVE_TICKET, MODE_LIVE_AUTO)
_SECRET_HINTS = ("key", "secret", "private", "seed", "mnemonic", "password", "token")


@dataclass(frozen=True)
class Config:
    # operation
    mode: str = MODE_SHADOW
    allow_live: bool = False
    data_dir: str = ""                 # runtime data (ledger/recorder); set at load
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    pumpportal_ws: str = "wss://pumpportal.fun/api/data"
    # Provider quota guard: Helius free tier is ~1M requests / 30 days
    # (~33k/day). Budget below that on purpose; see budget.py.
    rpc_daily_budget: int = 30000
    helius_api_daily_budget: int = 8000
    max_analyses_per_hour: int = 350
    # S0/S1 discovery + fast filter
    min_age_seconds: float = 120.0
    max_curve_pct: float = 40.0
    buffer_ttl_seconds: float = 900.0
    buffer_max: int = 2000
    seen_max: int = 20000
    # S3 safety
    top10_fail_pct: float = 0.25
    # S4 liquidity / slippage
    liquidity_multiple_min: float = 10.0
    liquidity_multiple_recommended: float = 20.0
    impact_reduce_above: float = 0.03
    impact_reject_above: float = 0.05
    max_slippage_bps: int = 300
    pool_drain_rug_pct: float = 0.30   # SOL drop in 5 min while holding -> rug-exit
    pool_drain_window_seconds: float = 300.0
    # S5 flow
    min_unique_buyers: int = 5
    min_wallet_diversity: float = 0.30
    bundle_penalty_above: float = 0.20
    bundle_reject_above: float = 0.35
    min_organic_share: float = 0.50
    # S6 creator
    block_creator_after_rugs: int = 1
    # S7 potential / regime
    min_total_score: float = 0.65
    weights_risk_inverse: float = 0.35
    weights_liquidity_curve: float = 0.20
    weights_wallet_diversity: float = 0.20
    weights_potential: float = 0.15
    weights_social: float = 0.10
    provider_risk_reject: float = 0.70  # normalised provider score > 7/10
    min_potential_score: float = 0.15   # too-quiet tokens are skipped too
    regime_cache_seconds: float = 900.0
    regime_stale_seconds: float = 1800.0
    # sizing / risk brakes (T-RISK) - values are SOL
    paper_capital_sol: float = 5.0
    max_position_sol: float = 0.25
    min_position_sol: float = 0.05
    daily_budget_sol: float = 1.0
    daily_loss_limit_sol: float = 0.25
    max_trades_per_day: int = 10
    max_open_positions: int = 3
    # exits (T-EXIT) - used from P2 on; defined now so config is complete
    stop_loss_pct: float = 0.30
    take_profit_pct: float = 0.50      # retuned 2026-11-10 (was 1.00)
    take_profit_fraction: float = 0.50
    trailing_pct: float = 0.25         # retuned 2026-11-10 (was 0.35)
    max_hold_seconds: float = 3600.0
    decay_exit_seconds: float = 1200.0
    exit_poll_seconds: float = 3.0
    # fees modelled in paper (T-COST)
    fee_pumpfun_pct: float = 0.01
    fee_pumpportal_pct: float = 0.005
    jito_tip_lamports: int = 10000
    base_fee_lamports: int = 5000

    def validate(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"unknown mode: {self.mode!r}")
        if self.mode in (MODE_LIVE_TICKET, MODE_LIVE_AUTO) and not self.allow_live:
            raise ValueError("live mode requires allow_live = true")
        w = (self.weights_risk_inverse + self.weights_liquidity_curve
             + self.weights_wallet_diversity + self.weights_potential
             + self.weights_social)
        if abs(w - 1.0) > 1e-9:
            raise ValueError(f"scoring weights must sum to 1.0, got {w}")
        if self.mode == MODE_SHADOW and self.allow_live:
            raise ValueError("shadow mode must never carry allow_live")


def load_config(path: str | Path | None, data_dir: str | Path) -> Config:
    """Load TOML if given, else defaults; always fail-closed on bad input."""
    raw: dict = {}
    if path is not None:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    # Sections map to flat keys by dropping the section name: thresholds are
    # unique across sections by design; a collision is a config error.
    flat: dict = {}
    for section, node in raw.items():
        if not isinstance(node, dict):
            raise ValueError(f"config section {section!r} must be a table")
    for section, node in raw.items():
        for k, v in node.items():
            if isinstance(v, dict):
                raise ValueError("nested tables deeper than one level are not supported")
            low = k.lower()
            if any(h in low for h in _SECRET_HINTS):
                raise ValueError(f"secret-looking key {k!r} must not appear in config")
            if k in flat:
                raise ValueError(f"duplicate config key across sections: {k!r}")
            flat[k] = v
    known = {f.name for f in fields(Config)} - {"data_dir"}
    unknown = set(flat) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    cfg = Config(**flat, data_dir=str(data_dir))
    cfg.validate()
    return cfg
