# PumpScout (P1 - shadow scanner)

Deterministic, no-AI memecoin scanner for pump.fun (Solana), built
clean-room from the approved design document (workspace/meme-scout).
P1 scope: discovery + analysis pipeline S0-S8 in **shadow mode only** -
decisions are recorded as WOULD_BUY / rejections with fixed reason
codes, then labelled against the real fate of each token at +1h/+6h/
+24h. **This code cannot sign or send anything; there is no executor.**

## Layout

| Module | Role |
| --- | --- |
| `discovery.py` | S0 PumpPortal WS stream (new tokens + migrations), bounded dedupe, WS_GAP recording |
| `filters.py` | S1 fast filter (metadata, age, curve %, creator blacklist) |
| `enrichment.py` | S2 reads: Solana RPC (mint authorities, bonding curve PDA, largest holders), pump.fun API, RugCheck; per-source health flags, blind = None |
| `safety.py` | S3 T-SAFE vetoes (mint/freeze authority, program, sell proxy) |
| `liquidity.py` | S4 T-LIQ/T-SLIP: real-SOL multiple, curve impact simulation |
| `flow.py` | S5 T-HOLDER/T-FLOW: concentration vetoes, diversity, bundle, sniper, coordination, wash |
| `creator.py` | S6 T-CREATOR: persistent creator store, blacklist after 1 rug |
| `potential.py` | S7 T-POT/T-TIMING: countable proxies + fail-closed regime gate |
| `scoring.py`, `risk.py` | S8 weighted score (config weights) + five risk brakes, score sizing |
| `pipeline.py` | Shadow orchestration S0-S8, one Decision per token |
| `ledger.py` | Append-only SQLite (events/tokens/analyses/decisions/fate_labels) |
| `recorder.py` | Daily JSONL recordings - the replay/backtest dataset |
| `fate.py` | Fate labeler: graduated / rug / dead / alive vs decision snapshot |
| `replay.py` | Offline re-run of S3-S8 over recorded analyses with a candidate config |
| `bot.py` | Scout process: stream + sweep + fate + regime + heartbeat |
| `monitor.py` | Read-only monitor: heartbeat stale > 60 s = severity-1 alert |
| `deploy/` | systemd unit master copy + `ensure_running.sh` (TrendMR pattern) |

## Run (dev / smoke)

```bash
pip install websockets            # only non-stdlib runtime dep besides requests
python -m pumpscout.bot --data-dir /path/to/runtime [--config pumpscout.toml]
python -m pumpscout.monitor --data-dir /path/to/runtime --once
python -m pumpscout.monitor --data-dir /path/to/runtime --funnel
python -m pumpscout.replay  --data-dir /path/to/runtime [--config candidate.toml]
python -m unittest discover -s pumpscout/tests -t .
```

Runtime data (ledger, recordings, heartbeat, creator store) lives in
`--data-dir`, **outside** the repo. Telegram alerts use env vars
`PUMPSCOUT_TG_BOT_TOKEN` / `PUMPSCOUT_TG_CHAT_ID` on the runtime machine
only. Config: see `config.example.toml`; the loader refuses unknown
keys, secret-looking keys, and live modes without `allow_live`.

## Verification status (2026-11-08, this VM)

- 20 unit tests pass (curve math, safety, flow, scoring, risk brakes,
  config fail-closed rules, mint/PDA parsing, full shadow pipeline).
- Live smoke (110 s): PumpPortal stream connected, tokens recorded.
- **Bonding-curve account layout VERIFIED on real data**: parsed
  virtual SOL 30.64 / real SOL 0.64 (difference = the standard 30 SOL
  virtual offset), total supply exactly 1e9, authorities revoked -
  the layout PumpGrok left as an assumption is confirmed.
- **Infrastructure blocker (evidence, not code)**: from this VM the
  pump.fun frontend API returns 403 and the public Solana RPC returns
  429 on `getTokenLargestAccounts`; most enrichments therefore end
  "blind" and are rejected `safety_blind` - the fail-closed design
  working as intended, but P1 measurement needs a private RPC
  (Helius free tier is enough). Key goes to the runtime env only,
  never into the repo.

## Provider quota discipline (Helius free tier ~1M requests / 30 days)

- S1 fast filter runs on stream data: **0 RPC requests**.
- Core enrichment is **1 request** per analysed token
  (`getMultipleAccounts` for mint + bonding curve).
- The holder read (**+1 request**) is paid only for tokens that pass
  the cheap contract/liquidity vetoes first.
- `budget.py` persists two daily buckets: **rpc 30,000/day** and
  **api 8,000/day** (the enhanced-transactions fallback that replaces
  the unreachable pump.fun frontend API). The pipeline paces analyses
  to **350/hour**. First live run (500 s, Helius): 62 analyses used
  67 rpc + 63 api requests, and the decision funnel produced real
  vetoes (safety_veto 29, impact_too_high 5) instead of blindness.
  Exhaustion fails closed: `data_unavailable / rpc_budget_exhausted`.
  Credit weights per call can differ by method, so check the Helius
  dashboard usage after the first full day and tune the buckets.
- P2 warning: position exit monitoring must use WebSocket account
  subscriptions, never polling - one position polled every 3 s would
  burn ~28.8k requests/day on its own.

## Known P1 limitations

- `sell_simulated_ok` is a curve-math exit proxy, not a real
  `simulateTransaction`; the real check lands with the P2 paper
  executor (documented deviation from design S3).
- Regime score is a stream heuristic (graduations/launches ratio);
  it is logged with every snapshot and tuned during P1.
- Holder percentages are computed over the largest-accounts sample
  returned by RPC, not the full holder set.
