#!/usr/bin/env bash
# Remote diagnostic: prom container mounts + file_sd config + targets visibility
set -u

echo "=== container mounts ==="
docker inspect llm-port-prometheus \
  --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{println}}{{end}}' 2>&1

echo "=== file_sd sections in container config ==="
docker exec llm-port-prometheus sh -c "grep -n -A2 'file_sd' /etc/prometheus/prometheus.yml" 2>&1

echo "=== targets.json visible inside container? ==="
docker exec llm-port-prometheus sh -c "ls -la /etc/prometheus/ 2>&1; head -c 200 /etc/prometheus/targets.json 2>&1"

echo "=== prom log (last reload / sd errors) ==="
docker logs llm-port-prometheus 2>&1 | grep -aE "reload|sd file|level=error|level=warn" | tail -10
