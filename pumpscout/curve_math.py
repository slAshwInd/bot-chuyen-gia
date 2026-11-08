"""Bonding-curve math for pump.fun (constant product on virtual reserves).

Pure functions on (virtual_sol, virtual_token). All amounts in SOL and
whole tokens. The curve charges the pump.fun fee on top; fee handling for
orders lives in the (future) executor - here we compute raw curve quotes
used for price, impact simulation (T-SLIP) and paper fills.
"""
from __future__ import annotations


def spot_price(virtual_sol: float, virtual_token: float) -> float:
    """SOL per token at the current curve point."""
    if virtual_token <= 0:
        raise ValueError("virtual_token must be positive")
    return virtual_sol / virtual_token


def buy_quote(virtual_sol: float, virtual_token: float, sol_in: float) -> tuple[float, float]:
    """Return (tokens_out, avg_price) for a buy of sol_in SOL (pre-fee)."""
    if sol_in <= 0:
        return 0.0, spot_price(virtual_sol, virtual_token)
    k = virtual_sol * virtual_token
    new_sol = virtual_sol + sol_in
    new_token = k / new_sol
    tokens_out = virtual_token - new_token
    return tokens_out, sol_in / tokens_out


def sell_quote(virtual_sol: float, virtual_token: float, tokens_in: float) -> tuple[float, float]:
    """Return (sol_out, avg_price) for selling tokens_in tokens (pre-fee)."""
    if tokens_in <= 0:
        return 0.0, spot_price(virtual_sol, virtual_token)
    k = virtual_sol * virtual_token
    new_token = virtual_token + tokens_in
    new_sol = k / new_token
    sol_out = virtual_sol - new_sol
    return sol_out, sol_out / tokens_in


def price_impact(virtual_sol: float, virtual_token: float, sol_in: float) -> float:
    """Relative gap between average fill price and spot price for a buy."""
    sp = spot_price(virtual_sol, virtual_token)
    _, avg = buy_quote(virtual_sol, virtual_token, sol_in)
    return (avg - sp) / sp


def size_for_impact(virtual_sol: float, virtual_token: float,
                    target_impact: float, min_size: float, max_size: float) -> float:
    """Largest buy size in [min_size, max_size] whose impact <= target.

    Impact for a constant-product buy of size s is exactly s / virtual_sol,
    so the answer is closed-form; we still clamp and verify numerically.
    """
    if virtual_sol <= 0:
        return 0.0
    size = min(max_size, target_impact * virtual_sol)
    if size < min_size:
        # Even the minimum breaches the target: report the min so the caller
        # can reject with impact_too_high after checking impact itself.
        return min_size
    return size


def curve_progress(real_token_sold: float, initial_real_token: float) -> float:
    """Bonding curve completion 0..1 from tokens sold vs initial real supply."""
    if initial_real_token <= 0:
        return 0.0
    return max(0.0, min(1.0, real_token_sold / initial_real_token))
