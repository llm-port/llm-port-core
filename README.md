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
   - `llm-port-node-agent`

## Environment Variables

- `LLM_PORT_NODE_AGENT_BACKEND_URL` default `http://127.0.0.1:8000`
- `LLM_PORT_NODE_AGENT_AGENT_ID` default `hostname`
- `LLM_PORT_NODE_AGENT_HOST` default `hostname`
- `LLM_PORT_NODE_AGENT_ADVERTISE_HOST` default `LLM_PORT_NODE_AGENT_HOST`
- `LLM_PORT_NODE_AGENT_ADVERTISE_SCHEME` default `http` (allowed: `http`, `https`)
- `LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN` one-time token for initial enrollment
- `LLM_PORT_NODE_AGENT_STATE_PATH` default `/var/lib/llm-port-node-agent/state.json`
- `LLM_PORT_NODE_AGENT_HEARTBEAT_INTERVAL_SEC` default `15`
- `LLM_PORT_NODE_AGENT_INVENTORY_INTERVAL_SEC` default `60`
- `LLM_PORT_NODE_AGENT_RECONNECT_MIN_SEC` default `2`
- `LLM_PORT_NODE_AGENT_RECONNECT_MAX_SEC` default `30`
- `LLM_PORT_NODE_AGENT_REQUEST_TIMEOUT_SEC` default `20`
- `LLM_PORT_NODE_AGENT_VERIFY_TLS` default `true`

## Systemd

A unit file template is available at:
- `deploy/systemd/llm-port-node-agent.service`
