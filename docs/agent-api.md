# Infra Agent API (Contract)

## Goal

Define multi-host execution contracts while keeping local execution as the v1 default.

## Endpoints

- `POST /api/admin/system/agents/register`
- `POST /api/admin/system/agents/heartbeat`
- `GET /api/admin/system/agents`
- `POST /api/admin/system/agents/{agent_id}/apply`
- `GET /api/admin/system/agents/{agent_id}/jobs/{job_id}`

## Notes

- Local adapter remains default in v1.
- Remote apply endpoint is contract-ready and feature-flagged by `LLM_PORT_BACKEND_SYSTEM_AGENT_ENABLED`.
- Optional bearer protection for agent calls uses `LLM_PORT_BACKEND_SYSTEM_AGENT_TOKEN`.
