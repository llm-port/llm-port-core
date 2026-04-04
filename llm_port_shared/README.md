# llm_port_shared Stack Ownership

## Config Ownership

Shared stack runtime configuration is now intended to be managed by backend system settings APIs and the frontend initialization wizard.

Primary flow:
1. Operator updates settings in `/admin/settings`.
2. Backend stores values/secrets and triggers immediate apply.
3. Local executor performs service restart or compose recreate as required.

## Notes

- `llm_port_shared/.env` remains supported as bootstrap fallback.
- v1 execution target defaults to `local`.
- Remote host execution is contract-ready via `/api/admin/system/agents/*`.
