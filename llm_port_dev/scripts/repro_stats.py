#!/usr/bin/env python3
"""Reproduce the backend's exact STAT_QUERIES against its exact prom_url."""
import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/home/sachi/llm-port-core/llm_port_backend")
from llm_port_backend.services.llm.monitoring import STAT_QUERIES  # noqa: E402
from llm_port_backend.settings import settings  # noqa: E402

print("llm_monitoring_enabled:", settings.llm_monitoring_enabled)
print("prom_url:", repr(settings.prom_url))
print("targets_file:", repr(settings.prom_targets_file))

rt = '{runtime_name="Qwen/Qwen3.8-27B-FP8"}'
for key, tpl in STAT_QUERIES.items():
    expr = tpl.format(rt=rt)
    url = f"{settings.prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        results = (payload.get("data") or {}).get("result") or []
        val = results[0]["value"][1] if results else None
        print(f"{key:26} status={payload.get('status')} value={val} err={payload.get('error','-')}")
    except Exception as e:  # noqa: BLE001
        print(f"{key:26} EXC {type(e).__name__}: {e}")
