# llm_port_mcp

MCP Tool Registry — governed MCP broker for llm.port.

Registers MCP-compliant servers (stdio + SSE), discovers tools automatically,
converts them to OpenAI-compatible tool definitions, and routes all tool
invocations through a Privacy Proxy (Presidio-based PII detection/redaction).

## Quick Start

```bash
uv sync
python -m llm_port_mcp
```

## Environment Variables

All settings use the `LLM_PORT_MCP_` prefix:

| Variable | Default | Description |
|---|---|---|
| `LLM_PORT_MCP_HOST` | `127.0.0.1` | Bind address |
| `LLM_PORT_MCP_PORT` | `8000` | Bind port |
| `LLM_PORT_MCP_DB_HOST` | `127.0.0.1` | PostgreSQL host |
| `LLM_PORT_MCP_DB_PORT` | `5432` | PostgreSQL port |
| `LLM_PORT_MCP_DB_USER` | `llm_user` | PostgreSQL user |
| `LLM_PORT_MCP_DB_PASS` | `llm_user` | PostgreSQL password |
| `LLM_PORT_MCP_DB_BASE` | `llm_mcp` | PostgreSQL database name |
| `LLM_PORT_MCP_REDIS_HOST` | `` | Redis host (empty = disabled) |
| `LLM_PORT_MCP_REDIS_PORT` | `6379` | Redis port |
| `LLM_PORT_MCP_ENCRYPTION_KEY` | `` | Fernet master key for secret encryption |
| `LLM_PORT_MCP_PII_SERVICE_URL` | `` | PII service base URL |
| `LLM_PORT_MCP_SERVICE_TOKEN` | `` | Shared token for internal API auth |
