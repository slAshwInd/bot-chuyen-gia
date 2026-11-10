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

0.3.1 (2026-11-10): call-mechanism optimisation after Helius answered
429 (monthly credit quota burned in ~3 days). The enhanced-API trade
read moves from enrich() - where it fired for nearly every analysis -
to a late pipeline phase paid only for tokens that survive S3/S4 and
the holder vetoes; the api budget now counts approximate credits
(~100 per enhanced call, 15k/day cap). A circuit breaker pauses
analysis after 10 consecutive core-RPC failures (10 min cooldown),
and fate passes are capped (300 labels), budget-charged and abort
after 5 consecutive unreadable curves. No threshold or rule changed.

0.3.2 (2026-11-11, user-approved option A): fate labeler scheduling.
The 0.3.1 pass shared one global cap in 1h -> 6h -> 24h order, so a
swollen 1h backlog starved the 6h/24h horizons indefinitely (6h
labels stalled ~22h, 24h never started). Each horizon now has its
own 150-label cap per pass, queues are ordered oldest-decision
first, and a mint whose curve read fails in 3 consecutive passes is
marked in fate_failures and drops to the low-priority tail of its
queue instead of being retried first forever; a successful read
clears the mark. Labelling only - no threshold or rule changed.
"""

__version__ = "0.3.2"
MODE_SHADOW = "shadow"
MODE_PAPER = "paper"
MODE_LIVE_TICKET = "live_ticket"
MODE_LIVE_AUTO = "live_auto"
