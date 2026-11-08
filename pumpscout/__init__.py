"""PumpScout - deterministic, no-AI memecoin scanner/trader for pump.fun (Solana).

P1 scope: discovery + analysis pipeline S0-S8 in SHADOW mode only.
No order is ever signed or sent in P1; decisions are recorded so the
filter quality can be measured against the real fate of each token.
"""

__version__ = "0.1.0"
MODE_SHADOW = "shadow"
MODE_PAPER = "paper"
MODE_LIVE_TICKET = "live_ticket"
MODE_LIVE_AUTO = "live_auto"
