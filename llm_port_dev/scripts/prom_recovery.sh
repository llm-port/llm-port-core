#!/usr/bin/env bash
# Remote: backend picked up the fix (WatchFiles restart -> rebuild_all in-place
# write to the CURRENT host inode), then restart the prom container so its
# bind mount re-attaches to the current host file, then verify targets.
set -u

cd /home/sachi/llm-port-core || exit 1
T=/home/sachi/llm-port-core/llm_port_shared/prometheus/targets.json

echo "=== host file BEFORE container restart (post backend-rebuild) ==="
stat -c '%i %s %n' "$T"
python3 -m json.tool "$T" | head -8

echo "=== restart prom container to re-bind mount to current inode ==="
docker stop llm-port-prometheus && docker start llm-port-prometheus
sleep 10

echo "=== container view of targets.json ==="
docker exec llm-port-prometheus sh -c "stat -c '%i %s' /etc/prometheus/targets.json; python3 -m json.tool /etc/prometheus/targets.json 2>/dev/null | head -8 || head -c 120 /etc/prometheus/targets.json"

echo "=== active targets via prom API ==="
curl -s "http://127.0.0.1:9095/api/v1/targets?state=active" > /tmp/tgt.json
python3 - <<'EOF'
import json
d = json.load(open("/tmp/tgt.json"))
ts = d.get("data", {}).get("activeTargets", [])
print(f"activeTargets: {len(ts)}")
for t in ts:
    print(" -", t["scrapeUrl"], "|", t["health"], "|", t["labels"].get("runtime_name", ""), "|", t.get("lastError", ""))
EOF
