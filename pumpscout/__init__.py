"""PumpScout - deterministic, no-AI memecoin scanner/trader for pump.fun (Solana).

P1 scope: discovery + analysis pipeline S0-S8 in SHADOW mode.
P2 scope: PAPER executor + position supervisor - accepted decisions
open simulated positions (curve-math fills, full fee model) that are
closed by the S10 exit rules, all recorded for forward evaluation.
No order is ever signed or sent; there is no live executor.

0.3.0 (2026-11-10, user-approved package B): S5 holder shares are
computed against total supply with owner wallets resolved and the
bonding curve excluded (they were shares of the top-20 sum over
token-account addresses, which no token could pass); unmeasurable
socials score 0 at S7/S8 instead of blocking every decision (safety
vetoes and blind checks stay fail-closed); exits retuned - TP stage 1
at +50%, trailing 25%, stop-loss unchanged at -30%. All entry
thresholds unchanged; the paper era from this version is evaluated
separately at REVIEW_HOLD 30.
"""

__version__ = "0.3.0"
MODE_SHADOW = "shadow"
MODE_PAPER = "paper"
MODE_LIVE_TICKET = "live_ticket"
MODE_LIVE_AUTO = "live_auto"
