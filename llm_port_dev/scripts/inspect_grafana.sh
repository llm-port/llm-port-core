#!/usr/bin/env bash
set -u
echo "=== env ==="
docker inspect llm-port-grafana --format '{{range .Config.Env}}{{println .}}{{end}}'
echo "=== mounts ==="
docker inspect llm-port-grafana --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
echo "=== ports ==="
docker inspect llm-port-grafana --format '{{range $p, $c := .NetworkSettings.Ports}}{{$p}} -> {{json $c}}{{println}}{{end}}'
