"""PumpScout - deterministic, no-AI memecoin scanner/trader for pump.fun (Solana).

P1 scope: discovery + analysis pipeline S0-S8 in SHADOW mode.
P2 scope: PAPER executor + position supervisor - accepted decisions
open simulated positions (curve-math fills, full fee model) that are
closed by the S10 exit rules, all recorded for forward evaluation.
No order is ever signed or sent; there is no live executor.
"""

__version__ = "0.2.0"
MODE_SHADOW = "shadow"
MODE_PAPER = "paper"
MODE_LIVE_TICKET = "live_ticket"
MODE_LIVE_AUTO = "live_auto"
