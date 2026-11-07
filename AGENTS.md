# Agent safety rules - BaseAster

- This is a SOURCE-ONLY Git workspace. The Windows runtime is its parent directory.
- NEVER read secrets directories, copies of secrets, credential stores or values. Never print credentials.
- NEVER commit/copy runtime config, state, trade/order history, pending closes, account data, logs, flags, cursors, archives, environments or backups.
- Synchronize using exact paths in SOURCE_ALLOWLIST.txt; .gitignore alone is insufficient. Inspect tracked paths, staged blobs, outgoing commits and diff. Run python source_guard.py before every commit/push. Never git add . or git add -A.
- The allowlisted ref/config_paper.json, monitor_config.example.json and tests/fixture_*.json are generic source/test templates ONLY. They are NOT deployment files. Never substitute live data into them. Do not broaden the allowlist automatically.
- Original deposit and balance are private runtime data, never source constants. Keep deposit in ignored local reporting config; distinguish original deposit from changing balance and sizing capital.
- Do not change trading principles, signal/risk/sizing rules, order execution, positions or pending-close logic. Reporting must not import/call trading or exchange clients.
- Source edits and GitHub push require the user's request. Deployment into runtime, service installation/restart and Telegram test sends require separate approval. Never run launchers, installer or emergency/trading scripts as a test.
- Read SAFE_UPDATE.md. Copy ONLY approved changed source files individually; never mirror, unzip or replace an entire directory. Do not copy config, fixtures, templates or ref into runtime.
- Old Git history may contain tracked config_live.json. New commit removal is NOT history sanitization; never claim it purges history. History rewrite and credential rotation require separate owner decisions.

Snapshot durability: a unique same-directory temp file is flushed/closed before replacement. Transient Windows sharing/access errors receive bounded retry. If the non-authoritative reporting snapshot still cannot be written, the old snapshot remains and a diagnostic is logged; the trading loop continues. This does not suppress failures of authoritative order journals/books. No claim is made that all filesystem/process failures are eliminated.

Unified-service request: read latest SAFE_UPDATE.md section. Never run legacy full installer to undo consolidation. A pre-stop flat LOOP cannot prove post-stop account flat. Activation requires fresh checks/elevation; source commit alone is not activation. Dashboard must remain manual. Do not assume host Running proves three healthy children.

Presentation reload: only reporting.py is hot-loaded, on the next summary. Follow SAFE_UPDATE.md exact source paths; no automatic GitHub pull, engine reload, state reset or service reinstallation. Bootstrap/reloader changes require approved Telegram/Monitor child reload; keep Trade running.
