"""S3 contract safety (task T-SAFE).

Direct on-chain facts decide; provider scores are only supporting
evidence. Any check that could not be read is "blind" and blind is a
rejection, never a pass. Vetoes are absolute and are evaluated before
any scoring (principle P3).
"""
from __future__ import annotations

from .config import Config
from .models import Analysis, SafetyReport


def evaluate_safety(a: Analysis, cfg: Config) -> SafetyReport:
    blind: list[str] = []

    if not a.authorities_read:
        blind.append("authorities")
    if a.token_program_ok is None:
        blind.append("token_program")
    if a.sell_simulated_ok is None:
        blind.append("sell_simulation")
    # Holders are read in a later, separate phase (quota) and are owned
    # by flow.evaluate_flow, which fails closed when they are unread.

    # Absolute vetoes (only on facts actually read).
    if a.authorities_read:
        if a.mint_authority is not None:
            return SafetyReport(veto="safety_veto", blind=tuple(blind),
                                risk_score=1.0)
        if a.freeze_authority is not None:
            return SafetyReport(veto="safety_veto", blind=tuple(blind),
                                risk_score=1.0)
    if a.token_program_ok is False:
        return SafetyReport(veto="safety_veto", blind=tuple(blind), risk_score=1.0)
    if a.sell_simulated_ok is False:
        return SafetyReport(veto="safety_veto", blind=tuple(blind), risk_score=1.0)
    if a.rugcheck_extreme:
        return SafetyReport(veto="safety_veto", blind=tuple(blind), risk_score=1.0)

    # Contract-side risk component only; holder concentration is folded
    # in by the pipeline after the late holder read (flow owns top-10).
    risk = a.rugcheck_score if a.rugcheck_score is not None else 0.0
    return SafetyReport(veto=None, blind=tuple(blind), risk_score=risk)
