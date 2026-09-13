#!/usr/bin/env bash
# Remote end-to-end: dev-login -> runtimes (monitoring fields) -> monitoring-stats
set -u

B=http://10.88.10.61:8001
TOK=$(curl -s -X POST "$B/api/auth/dev-login" -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"admin"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
echo "token: ${TOK:0:12}..."

curl -s "$B/api/llm/runtimes/" -H "Authorization: Bearer $TOK" > /tmp/rt.json
python3 - <<'PYEOF'
import json
d = json.load(open("/tmp/rt.json"))
rows = d if isinstance(d, list) else d.get("runtimes", d.get("items", []))
print(f"runtimes: {len(rows)}")
for r in rows:
    m = r.get("monitoring")
    print(" -", (r.get("name") or "?"), "| status:", r.get("status"),
          "| monitoring:", (m or {}).get("enabled"), "| dash:", (m or {}).get("dashboard_url"))
PYEOF

# first runtime id -> stats
RID=$(python3 -c 'import json;d=json.load(open("/tmp/rt.json"));rows=d if isinstance(d,list) else d.get("runtimes",d.get("items",[]));print(rows[0]["id"])')
echo "--- stats for $RID ---"
curl -s "$B/api/llm/runtimes/$RID/monitoring-stats" -H "Authorization: Bearer $TOK" | python3 -m json.tool
