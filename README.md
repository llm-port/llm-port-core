# llm-port-core

> Self-hosted all-in-one LLM platform — gateway, chat console, control plane, and optional modules in a single release.

This monorepo contains all **core** (Apache 2.0) components of [llm.port](https://llm-port.github.io).

## Components

| Directory             | Description                              | Runtime                    |
| --------------------- | ---------------------------------------- | -------------------------- |
| `llm_port_backend`    | Control plane API (FastAPI)              | Docker                     |
| `llm_port_frontend`   | React admin console                      | Docker                     |
| `llm_port_api`        | OpenAI-compatible gateway                | Docker                     |
| `llm_port_pii`        | PII detection & redaction (Presidio)     | Docker (profile: `pii`)    |
| `llm_port_mcp`        | MCP tool registry                        | Docker (profile: `mcp`)    |
| `llm_port_skills`     | Skills registry                          | Docker (profile: `skills`) |
| `llm_port_shared`     | Compose files, nginx, base image, initdb | Docker                     |
| `llm_port_cli`        | CLI installer & management tool          | PyPI / pipx                |
| `llm_port_node_agent` | Remote node execution agent              | Standalone binary          |

## Quick Start

```bash
pip install llmport
llmport init
llmport deploy
```

## Docker Images

All core images are published to Docker Hub under the `llmport` organisation:

- `llmport/api`
- `llmport/backend`
- `llmport/frontend`
- `llmport/pii`
- `llmport/mcp`
- `llmport/skills`

Images are tagged with `latest` on main and with semver on release tags (e.g. `llmport/api:v1.0.0`).

## Building from source

```bash
cd llm_port_shared
docker compose build
```

## Node Agent

The node agent is distributed as standalone binaries (no Python required).
See [llm_port_node_agent/README.md](llm_port_node_agent/README.md) and the
[Releases](https://github.com/llm-port/llm-port-core/releases) page.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
