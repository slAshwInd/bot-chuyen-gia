#!/bin/bash
# Revive the PumpScout shadow service if needed. Safe to run repeatedly.
# Mirrors the TrendMR pattern: reinstalls the unit from this workspace
# master copy if a VM replacement wiped /etc, restarts when inactive or
# when the heartbeat is stale (>60s - PumpScout severity-1 threshold).
# Deployment itself (first install) is a separate owner-approved action.
set -u
UNIT=pumpscout
DIR=/home/hatch/workspace/crypto-strategy/bot-v2/pumpscout/deploy
RUNTIME=/home/hatch/pumpscout-runtime
HB=$RUNTIME/heartbeat.json

for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; do
  val="${!v:-}"
  [ -n "$val" ] && systemctl set-environment "$v=$val"
done

if [ ! -f /etc/systemd/system/$UNIT.service ]; then
  cp "$DIR/$UNIT.service" /etc/systemd/system/ && systemctl daemon-reload
fi
systemctl enable $UNIT >/dev/null 2>&1

ACTIVE=$(systemctl is-active $UNIT 2>/dev/null || echo inactive)
STALE=$(python3 - "$HB" <<'EOF'
import json, sys, time
try:
    hb = json.load(open(sys.argv[1]))
    print('1' if time.time() - float(hb.get('ts', 0)) > 60 else '0')
except Exception:
    print('1')
EOF
)
if [ "$ACTIVE" != "active" ] || [ "$STALE" = "1" ]; then
  systemctl restart $UNIT
  echo "RESTARTED (was active=$ACTIVE stale=$STALE)"
else
  echo "OK (active, heartbeat fresh)"
fi
