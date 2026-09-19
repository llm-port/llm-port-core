# Ray migration — live smoke test

End-to-end check of the Ray inference path on real hardware, driven by the
**product path**: the harness only creates rows and watches; the backend's own
reconciler loop and a real node agent running real Ray do all the work.

```
enroll → environment ready → ModelAvailability ready → deployment running
       → published OpenAI endpoint answers off-node → reconcile is observe-only
       → delete → environment stop confirmed
```

Unit tests alone are not evidence here: on 2026-09-19 the suites were green
while this path was broken end to end (their fakes bypassed the production
wiring). Run this before calling a Ray change done.

## Prerequisites

- Docker with an NVIDIA GPU (`--gpus all`), compute capability ≥ 7.5 (the
  pinned torch 2.11/cu130 wheels ship sm_75+). Verified on a TITAN RTX under
  Docker Desktop/WSL2.
- The shared stack's Postgres container running (default name
  `llm-port-postgres`, reachable on `127.0.0.1:5432`), and the backend venv
  (`llm_port_backend/.venv`).
- About 15 GB for the agent image, and internet access for the model download.
- Free host ports: 8000 (backend), 5673 (broker), 18000 (Serve, head → host).

Everything runs from Git Bash on Windows or any bash on Linux.

## Runbook

```bash
cd llm_port_backend/scripts/ray_smoke

./smoke.sh build-image        # once: python:3.12 + ray[serve,llm]==2.58.0 (vLLM 0.26.0)
./smoke.sh broker             # throwaway RabbitMQ on 127.0.0.1:5673
./smoke.sh reset-db           # fresh llm_port_smoke DB at the migration head
./smoke.sh backend            # separate terminal; leave running

./smoke.sh enroll             # agent enrolls via /nodes/enroll and heartbeats
./smoke.sh h create-env       # control plane + environment + head membership
./smoke.sh h watch-env --seconds 300 --until ready failed   # expect: "ready", HeadActive/ServeReady

./smoke.sh pull-model         # weights → /models/qwen2.5-0.5b-instruct on the head
./smoke.sh h seed-model       # LLMModel + ModelAvailability(ready, root_path)
./smoke.sh h create-deployment
./smoke.sh h watch-dep        # expect: one run_serve_app, then phase "running"
./smoke.sh check-endpoint     # expect: GET /models OK + a completion, from another container

./smoke.sh replica-starts     # note the count
./smoke.sh h request-reconcile
./smoke.sh h watch-dep --until-observed  # wait for the loop to re-observe it
./smoke.sh replica-starts     # expect: unchanged (observe-only, no redeploy)

./smoke.sh h set-desired deployment deleted
./smoke.sh h watch-dep        # expect: "deleted", endpoint retired
./smoke.sh h set-desired environment stopped
./smoke.sh h watch-env --seconds 180 --until stopped   # only after stop_ray succeeded

./smoke.sh cleanup            # stop the backend with Ctrl-C
```

`./smoke.sh h show` prints the environment, the deployment and the published
endpoint at any point.

### Reference result (2026-09-19, TITAN RTX, single node)

| Check | Result |
|---|---|
| Environment via loop | `ready` in one pass after the agent came up; token-auth attach OK |
| Deploy | 1 × `run_serve_app` (25 s, non-blocking), `running` after the model loaded, `ready_replicas: 1` |
| Endpoint | `http://<head-ip>:8000/llmport-<id>/v1` answered from a separate container |
| Reconcile a healthy deployment | 0 new `run_serve_app`, 0 replica restarts |
| Teardown | app deleted (route 404), endpoint `retired`, `stopped` after `stop_ray` succeeded |

## Diagnostics

When the product path fails, these isolate the layer:

- `h reconcile-dep-product --timeout 150` — calls the shipped
  `reconciliation.reconcile_deployment` seam directly.
- `h reconcile-env-shim` / `h reconcile-dep-shim` — drive the real managers
  with a node-control shim that commits each command and re-reads it fresh.
  If the shim works and the loop does not, the fault is in the backend's
  transaction/command handling, not in the agent or Ray.
- Agent side: `docker logs smoke-agent`; Ray Serve replica logs live under
  `/tmp/ray/session_latest/logs/serve/` in the container.

## Platform notes

- **WSL2 / Docker Desktop:** vLLM ≥ 0.26 refuses to start on WSL
  (`RuntimeError: UVA is not available`) unless `VLLM_WSL2_ENABLE_PIN_MEMORY=1`.
  `create-env` sets it through the environment's `config.node_env_vars` (passed
  to `ray start`, inherited by every Ray worker); it is ignored off WSL.
- **`host.docker.internal`** is not resolvable in plain `docker run`
  containers; the agent is started with `--add-host host.docker.internal:host-gateway`.
- **Broker:** the shared stack's RabbitMQ is not used (its credentials may not
  match the `.env` files); `broker` starts an isolated one with an explicit
  Erlang cookie.
- **Single backend worker:** every uvicorn worker runs the reconciler loop;
  only the advisory-lock holder acts. One worker keeps the logs readable.

## Configuration

| Variable | Default |
|---|---|
| `RAY_SMOKE_WORKDIR` | `./.work` (state, token, JWT secret, backend log; git-ignored) |
| `RAY_SMOKE_IMAGE` | `llmport-agent-ray-smoke:2.58.0` |
| `RAY_SMOKE_PG_CONTAINER` | `llm-port-postgres` |
| `RAY_SMOKE_DB` / `RAY_SMOKE_DB_OWNER` | `llm_port_smoke` / `llm_port_backend` |
| `RAY_SMOKE_BROKER_PORT` / `RAY_SMOKE_BACKEND_PORT` / `RAY_SMOKE_SERVE_HOST_PORT` | `5673` / `8000` / `18000` |
| `RAY_SMOKE_MODEL_REPO` | `Qwen/Qwen2.5-0.5B-Instruct` |

Volumes `smoke-models` (weights) and `smoke-agent-state` (node credential)
persist between runs; `reset-db` clears the latter.
