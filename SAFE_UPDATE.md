# BaseAster - safe update and reporting notes

## Boundaries
Source workspace: C:\Users\personal\baseaster\upcode.
Runtime: C:\Users\personal\baseaster. Deployment must use exact approved source files only; restart is a separate owner-approved operation.
GitHub owner: slAshwInd; repository: bot-chuyen-gia.

This workspace is source plus documentation and generic test templates. It does not contain runtime configuration, credentials, positions, pending orders or account history. SOURCE_ALLOWLIST.txt is an explicit path policy, not a wildcard copy list.

## Reporting changes
- /status and Monitor prefer BaseAsterTrade Service. The old Scheduled Task is consulted only if the Service is absent. Query denial is unknown, not Disabled. Python venv parent/child launcher chains count as one application leaf, not two bots; multiple independent leaves are abnormal. Heartbeat freshness and Sync/Halted are shown separately; Running alone is not proof of healthy trading or 100% uptime.
- Installer SOURCE now declares a fourth service, BaseAsterMonitor, using monitor_bnb.py. The installer has NOT been run. Never run it from upcode (it targets its own directory).
- Summary cadence is 120 minutes, regardless of legacy monitor_config interval. Migration starts a new 120-minute window on first launch; persisted schedule survives subsequent restarts. Missed windows are combined and labeled with their actual span. An unsent summary is queued with schedule state before delivery and retried every monitor tick. Telegram/network failures or stopped Windows prevent guaranteed on-time delivery. Crash after successful send but before final state persistence may duplicate a message (at-least-once intent, not exactly-once).
- Counts use retained logs only. Quet (LOOP) is a completed main decision cycle, not number of symbols, exchange API calls or background shortlist refreshes. Entry counts are recorded ENTRY/STEP3_ENTRY/BATDAY_ENTRY events, not independently reconciled exchange fills. Exact duplicate event records are collapsed.
- Vong khong ghi Entry is an ESTIMATE: entries map to the next completed LOOP within 120 seconds. Fast-loop events, clock changes, missing/rotated logs and boundary events can prevent exact attribution. Block reason totals are logged events, not rejected orders or distinct signals. No reason is invented when absent from logs.
- Reports stream all retained log records in [start,end), not an arbitrary tail byte limit. They cannot recover deleted/rotated-away history.
- /pnl uses state account balance/equity/upnl and config.start_capital when finite and positive. If absent/invalid, use ignored local monitor_config.initial_deposit. If neither is valid, deposit/return denominator is unknown, never a hardcoded account amount. Runtime deposit values are never committed to source. scaling.capital_usd/live.base are sizing values, NOT the original deposit.
- Balance minus deposit is labeled UNRECONCILED account change. CSV net is labeled as the sum of retained closed/partial-close records, not a full account PnL. Missing CSV is unknown, not zero or paper PnL. Fee/funding/transfer completeness cannot be established from current CSV alone. No exchange access is added. Full account reconciliation remains pending authoritative ledger evidence.

## Offline checks (no credentials/Telegram/exchange)
python test_reporting.py
python source_guard.py
Check PowerShell syntax with the parser only; do not execute service scripts.

## Deployment ONLY after explicit separate approval
1. Record exact approved commit/diff and service paths. Confirm no auto-deployment on GitHub push.
2. Do not read secrets. Record only hashes of existing non-secret config/state/history if needed; do not overwrite them. Account for live state files changing naturally.
3. For this fix, the only files to consider copying individually are: reporting.py, mon_common.py, monitor_bnb.py, tg_live_poller.py, snapshot_io.py, bnb_bot.py, install_services_bnb.ps1, verify_services_bnb.ps1. Installer/verification scripts are source for a separately approved service action, not commands to execute automatically.
4. NEVER copy .git, upcode, AGENTS.md, SOURCE_ALLOWLIST.txt, source_guard.py, test files, ref, fixture/example JSON, config_live.json, monitor_config.json, state*, trades*, events*, alerts*, secrets, logs, data, backups, flags or Telegram cursors into runtime. Never Copy-Item -Recurse, robocopy /MIR, bulk ZIP extract, git checkout or git reset in runtime.
5. The later snapshot durability fix additionally changes only the state_live.json snapshot write in bnb_bot.py and adds snapshot_io.py; no order journal, trading rule or execution code is changed. Review current baseline source changes separately rather than blanket-copying all repository files.
6. Service setup/restart requires owner approval. First verify there is no unmanaged monitor process or legacy monitor task; do NOT start a second instance. Existing installer has broad Trade/Telegram migration behavior: do not run the full installer merely to enable Monitor without reviewing and separately approving its effects. Prefer a separately reviewed Monitor-only setup.
7. After separately approved activation, verify Service status, fresh heartbeat, Sync/Halted, /status and /pnl, then wait for one real 120-minute report. No trading command is part of verification. Live delivery has NOT been tested here.

## Known limitation / deployment gate
run_bnb.ps1 still invokes rescale_config.py at startup. That script can recreate runtime config from the paper template and drop custom fields. This change deliberately does NOT alter startup rescaling/sizing or order logic. Do not restart Trade or deploy launchers as part of reporting activation. A separately approved fix/review of configuration regeneration is required before a Trade restart. .gitignore and source-only copying do NOT fix this existing runtime behavior.

config_live.json was tracked in old Git commits. It is removed from the new source-only tree only; existing history remains unchanged. No history purge or credential exposure assessment is claimed.

Snapshot durability: a unique same-directory temp file is flushed/closed before replacement. Transient Windows sharing/access errors receive bounded retry. If the non-authoritative reporting snapshot still cannot be written, the old snapshot remains and a diagnostic is logged; the trading loop continues. This does not suppress failures of authoritative order journals/books. No claim is made that all filesystem/process failures are eliminated.
