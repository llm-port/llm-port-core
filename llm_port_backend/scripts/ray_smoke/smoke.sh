#!/usr/bin/env bash
# Ray-migration live smoke test driver.  See README.md for the runbook.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
ROOT="$(cd "$BACKEND/.." && pwd)"

IMAGE="${RAY_SMOKE_IMAGE:-llmport-agent-ray-smoke:2.58.0}"
PG_CONTAINER="${RAY_SMOKE_PG_CONTAINER:-llm-port-postgres}"
DB="${RAY_SMOKE_DB:-llm_port_smoke}"
DB_OWNER="${RAY_SMOKE_DB_OWNER:-llm_port_backend}"
BROKER_PORT="${RAY_SMOKE_BROKER_PORT:-5673}"
BACKEND_PORT="${RAY_SMOKE_BACKEND_PORT:-8000}"
SERVE_HOST_PORT="${RAY_SMOKE_SERVE_HOST_PORT:-18000}"
MODEL_REPO="${RAY_SMOKE_MODEL_REPO:-Qwen/Qwen2.5-0.5B-Instruct}"
MODEL_ROOT="/models/qwen2.5-0.5b-instruct"  # must match harness.MODEL_ROOT

# Git Bash on Windows: keep MSYS from rewriting container paths, and hand
# docker/python host paths in a form they understand (C:/... — a /c/... path
# would reach a Windows python.exe as C:\c\...).  No-op on Linux.
export MSYS_NO_PATHCONV=1
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
venv_bin() { if [ -x "$BACKEND/.venv/Scripts/$1.exe" ]; then echo "$BACKEND/.venv/Scripts/$1.exe"; else echo "$BACKEND/.venv/bin/$1"; fi; }
PY="$(venv_bin python)"

WORK="${RAY_SMOKE_WORKDIR:-$HERE/.work}"
mkdir -p "$WORK"
WORK="$(host_path "$WORK")"
export RAY_SMOKE_WORKDIR="$WORK"

users_secret() {
  [ -s "$WORK/users_secret" ] || "$PY" -c "import secrets; print(secrets.token_hex(24))" > "$WORK/users_secret"
  cat "$WORK/users_secret"
}

backend_env() {
  export LLM_PORT_BACKEND_DB_BASE="$DB"
  export LLM_PORT_BACKEND_RABBIT_HOST=127.0.0.1 LLM_PORT_BACKEND_RABBIT_PORT="$BROKER_PORT"
  export LLM_PORT_BACKEND_RABBIT_USER=smoke LLM_PORT_BACKEND_RABBIT_PASS=smoke
  USERS_SECRET="$(users_secret)"
  export USERS_SECRET
  PYTHONPATH="$(host_path "$BACKEND")"
  export PYTHONPATH PYTHONIOENCODING=utf-8
}

harness() { (backend_env; cd "$BACKEND"; "$PY" -u "$(host_path "$HERE/harness.py")" "$@"); }

start_agent() {
  docker rm -f smoke-agent >/dev/null 2>&1 || true
  # --init reaps Ray's child processes (the agent would otherwise be PID 1).
  docker run -d --name smoke-agent --init --gpus all --shm-size=8g \
    --add-host host.docker.internal:host-gateway \
    -p "127.0.0.1:$SERVE_HOST_PORT:8000" \
    -v "$(host_path "$ROOT/llm_port_node_agent"):/src:ro" \
    -v smoke-models:/models \
    -v smoke-agent-state:/var/lib/llmport-agent \
    -e LLM_PORT_NODE_AGENT_BACKEND_URL="http://host.docker.internal:$BACKEND_PORT" \
    -e LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN="$(cat "$WORK/enroll_token" 2>/dev/null || true)" \
    -e LLM_PORT_NODE_AGENT_AGENT_ID=smoke-head \
    -e LLM_PORT_NODE_AGENT_STATE_PATH=/var/lib/llmport-agent/state.json \
    -e LLM_PORT_NODE_AGENT_MODEL_STORE=/models \
    -e HF_HOME=/models/hf \
    "$IMAGE" python -m llm_port_node_agent run >/dev/null
  echo "smoke-agent started ($(host_path "$ROOT/llm_port_node_agent") mounted read-only)"
}

usage() {
  cat <<'USAGE'
usage: smoke.sh <command>

  build-image      build the agent image (pinned ray[serve,llm] + vLLM)
  broker           start a throwaway RabbitMQ for the smoke backend
  reset-db         recreate the smoke DB at the migration head; reset agent state
  backend          run the backend (foreground, single worker) against the smoke DB
  enroll           mint an enrollment token, start the agent, wait for its heartbeat
  agent            (re)start the agent container with its persisted credential
  pull-model       download the model weights onto the head (bypasses the puller)
  check-endpoint   call the published base_url from a separate container
  replica-starts   count LLMServer replica starts on the head (restart check)
  h <stage...>     run a harness stage (python harness.py --help)
  cleanup          remove the agent and broker containers
USAGE
}

case "${1:-help}" in
  build-image)
    docker build -t "$IMAGE" -f "$HERE/Dockerfile.agent" "$HERE" ;;
  broker)
    docker rm -f smoke-rmq >/dev/null 2>&1 || true
    # Explicit cookie: a fresh data dir otherwise fails on .erlang.cookie (eacces).
    docker run -d --name smoke-rmq -p "127.0.0.1:$BROKER_PORT:5672" \
      -e RABBITMQ_DEFAULT_USER=smoke -e RABBITMQ_DEFAULT_PASS=smoke \
      -e RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS="-setcookie smoke" \
      -e RABBITMQ_CTL_ERL_ARGS="-setcookie smoke" \
      rabbitmq:4.2.1-management-alpine >/dev/null
    for _ in $(seq 1 40); do
      docker exec smoke-rmq rabbitmqctl authenticate_user smoke smoke >/dev/null 2>&1 && break
      sleep 2
    done
    echo "smoke-rmq ready on 127.0.0.1:$BROKER_PORT" ;;
  reset-db)
    docker exec "$PG_CONTAINER" psql -U postgres -q \
      -c "DROP DATABASE IF EXISTS $DB WITH (FORCE);" -c "CREATE DATABASE $DB OWNER $DB_OWNER;"
    docker exec "$PG_CONTAINER" psql -U postgres -q -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS vector;"
    (backend_env; cd "$BACKEND"; "$(venv_bin alembic)" upgrade head)
    docker volume rm smoke-agent-state >/dev/null 2>&1 || true
    rm -f "$WORK/state.json" "$WORK/enroll_token"
    echo "fresh $DB at the migration head; agent state reset" ;;
  backend)
    # One worker = one reconciler loop (extra workers would idle on the lock).
    backend_env
    export LLM_PORT_BACKEND_RELOAD=false LLM_PORT_BACKEND_WORKERS_COUNT=1
    export LLM_PORT_BACKEND_HOST=0.0.0.0 LLM_PORT_BACKEND_PORT="$BACKEND_PORT"
    export PYTHONUNBUFFERED=1 NO_COLOR=1
    cd "$BACKEND"
    "$PY" -m llm_port_backend 2>&1 | tee "$WORK/backend.log" ;;
  enroll)
    harness enroll-token | tail -1 > "$WORK/enroll_token"
    start_agent
    harness wait-node --timeout 120 ;;
  agent)
    start_agent ;;
  pull-model)
    docker exec smoke-agent hf download "$MODEL_REPO" --local-dir "$MODEL_ROOT" ;;
  check-endpoint)
    base="$(harness show | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["endpoints"][0]["published"]["base_url"])')"
    echo "published base_url: $base"
    # From a separate container: proves the endpoint is reachable off the head.
    docker run --rm --entrypoint sh "$IMAGE" -c "
      curl -sf -m 20 '$base/models' >/dev/null && echo 'GET /models: OK' &&
      curl -sf -m 120 '$base/chat/completions' -H 'Content-Type: application/json' \
        -d '{\"model\":\"Qwen2.5-0.5B-Instruct\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: smoke test passed\"}],\"max_tokens\":12,\"temperature\":0}'"
    echo ;;
  replica-starts)
    docker exec smoke-agent sh -c \
      'grep -h "Started initializing replica" /tmp/ray/session_latest/logs/serve/replica_*LLMServer*.log 2>/dev/null | wc -l' ;;
  h)
    shift
    harness "$@" ;;
  cleanup)
    docker rm -f smoke-agent smoke-rmq >/dev/null 2>&1 || true
    echo "removed smoke-agent and smoke-rmq (image, volumes and $DB kept)" ;;
  *)
    usage ;;
esac
