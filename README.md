# llm_port_node_agent

`llm_port_node_agent` is the host-side execution bridge for llm-port node clusters.

It does not perform cluster scheduling. The backend remains authoritative for:

- desired state
- placement and routing policy
- GPU-aware filtering and load-balancing

The agent responsibilities are:

- enroll with one-time token
- maintain authenticated outbound stream
- execute node commands (Docker runtime lifecycle)
- report heartbeat, inventory, command timeline, and events

## Quick Start

1. Create token in backend admin:
   - `POST /api/admin/system/nodes/enrollment-tokens`
2. Set environment variables on node host:
   - `LLM_PORT_NODE_AGENT_BACKEND_URL`
   - `LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN`
   - `LLM_PORT_NODE_AGENT_AGENT_ID`
   - `LLM_PORT_NODE_AGENT_HOST`
3. Start service:
   - `llmport-agent`

## Environment Variables

- `LLM_PORT_NODE_AGENT_BACKEND_URL` default `http://127.0.0.1:8000`
- `LLM_PORT_NODE_AGENT_AGENT_ID` default `hostname`
- `LLM_PORT_NODE_AGENT_HOST` default `hostname`
- `LLM_PORT_NODE_AGENT_ADVERTISE_HOST` default `LLM_PORT_NODE_AGENT_HOST`
- `LLM_PORT_NODE_AGENT_ADVERTISE_SCHEME` default `http` (allowed: `http`, `https`)
- `LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN` one-time token for initial enrollment
- `LLM_PORT_NODE_AGENT_STATE_PATH` default `/var/lib/llmport-agent/state.json`
- `LLM_PORT_NODE_AGENT_HEARTBEAT_INTERVAL_SEC` default `15`
- `LLM_PORT_NODE_AGENT_INVENTORY_INTERVAL_SEC` default `60`
- `LLM_PORT_NODE_AGENT_RECONNECT_MIN_SEC` default `2`
- `LLM_PORT_NODE_AGENT_RECONNECT_MAX_SEC` default `30`
- `LLM_PORT_NODE_AGENT_REQUEST_TIMEOUT_SEC` default `20`
- `LLM_PORT_NODE_AGENT_VERIFY_TLS` default `true`
- `LLM_PORT_NODE_AGENT_LOKI_URL` Loki push endpoint (e.g. `http://10.0.0.1:3100`). When set, the agent collects system logs (journald on Linux, Event Log on Windows) and pushes them to Loki with labels `{job="node-agent", host="<hostname>", level="..."}`.
- `LLM_PORT_NODE_AGENT_LOG_BATCH_SIZE` default `100` — max log lines per collection cycle
- `LLM_PORT_NODE_AGENT_LOG_FLUSH_INTERVAL_SEC` default `5` — seconds between log collection cycles

## Binary Installation (no Python required)

Pre-built standalone binaries are available on the
[GitHub Releases](https://github.com/llm-port/llm-port-node-agent/releases) page
for Linux (x86_64), Windows (x86_64), and macOS (universal).

### Linux

```bash
curl -fLO https://github.com/llm-port/llm-port-node-agent/releases/latest/download/llmport-agent-linux-x86_64
sudo install -m 0755 llmport-agent-linux-x86_64 /usr/local/bin/llmport-agent
```

Then install the systemd service (see below) or run directly:

```bash
export LLM_PORT_NODE_AGENT_BACKEND_URL=http://your-backend:8000
export LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN=tok_xxx
llmport-agent
```

### Windows

Download `llmport-agent-windows-x86_64.exe` from the releases page and run:

```powershell
$env:LLM_PORT_NODE_AGENT_BACKEND_URL = "http://your-backend:8000"
$env:LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN = "tok_xxx"
.\llmport-agent-windows-x86_64.exe
```

### macOS

```bash
curl -fLO https://github.com/llm-port/llm-port-node-agent/releases/latest/download/llmport-agent-macos-universal
chmod +x llmport-agent-macos-universal
export LLM_PORT_NODE_AGENT_BACKEND_URL=http://your-backend:8000
export LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN=tok_xxx
./llmport-agent-macos-universal
```

### Via CLI

```bash
llmport node agent deploy --backend-url http://your-backend:8000
```

The CLI auto-detects pre-built binaries in the workspace `dist/` directories and
uses them instead of cloning + setting up a Python venv.

## Systemd

A unit file template is available at:

- `deploy/systemd/llmport-agent.service`
