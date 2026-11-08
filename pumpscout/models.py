"""Core data model for PumpScout.

An Analysis is an immutable snapshot: every input used by a decision is
frozen at analysis time so any decision can be replayed from the ledger.
Missing data is represented explicitly (None / health flags), never by a
silent default - "blind is not pass" (design principle P2/P3).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NewToken:
    """S0 discovery event from the PumpPortal stream."""
    mint: str
    creator: str
    name: str = ""
    symbol: str = ""
    uri: str = ""
    created_ts: float = 0.0          # unix seconds, token creation (stream)
    seen_ts: float = 0.0             # unix seconds, when we received it
    initial_buy_sol: float = 0.0     # creator's own first buy, SOL
    curve_pct: float | None = None   # bonding curve progress 0..100 if known


@dataclass(frozen=True)
class Trade:
    wallet: str
    is_buy: bool
    sol_amount: float
    token_amount: float
    ts: float
    slot: int = 0


@dataclass(frozen=True)
class HolderSlice:
    wallet: str
    pct: float                       # share of total supply, 0..1


@dataclass(frozen=True)
class Analysis:
    """Everything enrichment (S2) managed to read, with health flags."""
    mint: str
    ts: float
    # S2 curve state (None = blind)
    virtual_sol: float | None = None
    virtual_token: float | None = None
    real_sol: float | None = None
    real_token: float | None = None
    complete: bool = False                       # graduated off the curve
    supply_tokens: float | None = None           # total supply, whole tokens
    # S3 contract facts (None = blind)
    mint_authority: str | None = None            # None value + read ok = revoked
    freeze_authority: str | None = None
    authorities_read: bool = False
    token_program_ok: bool | None = None
    sell_simulated_ok: bool | None = None
    rugcheck_score: float | None = None          # 0..1 normalised, 1 = worst
    rugcheck_extreme: bool = False
    # S5 holders / flow
    top_holders: tuple[HolderSlice, ...] = ()
    holders_read: bool = False
    trades: tuple[Trade, ...] = ()
    trades_read: bool = False
    # S6 creator history
    creator_prior_rugs: int = 0
    creator_prior_launches: int = 0
    creator_blacklisted: bool = False
    # S7 potential proxies (None = blind)
    has_socials: bool | None = None
    reply_count: int | None = None
    # bookkeeping
    health: tuple[str, ...] = ()                 # per-source flags, e.g. "rpc:ok"

    def is_blind(self, *fields: str) -> bool:
        return any(getattr(self, f) is None for f in fields)


@dataclass(frozen=True)
class SafetyReport:
    veto: str | None = None                      # reason_code when vetoed
    blind: tuple[str, ...] = ()                  # checks that could not be read
    risk_score: float = 0.0                      # 0..1, 1 = worst (measurables only)

    @property
    def passed(self) -> bool:
        return self.veto is None and not self.blind


@dataclass(frozen=True)
class Decision:
    mint: str
    ts: float
    stage: str                                   # last stage reached, e.g. "S5"
    accepted: bool                               # True = shadow WOULD_BUY
    reason_code: str                             # fixed catalog code
    score: float | None = None
    breakdown: tuple[tuple[str, float], ...] = ()
    detail: str = ""
