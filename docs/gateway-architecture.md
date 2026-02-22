# Gateway Architecture (MVP)

## Purpose
`llm_port_api` is a separate OpenAI-compatible gateway that routes `/v1/*` traffic to model providers.

## Public Endpoints
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/embeddings`

## Request Flow
1. Validate JWT bearer token.
2. Read `sub` (user id) and `tenant_id` claim.
3. Validate core request fields (`model`, `messages` / `input`).
4. Load tenant policy from Postgres.
5. Resolve model alias -> pool candidates.
6. Enforce RPM/TPM limits with Redis.
7. Acquire distributed concurrency lease in Redis.
8. Proxy request to selected upstream instance.
9. Retry once if non-stream upstream call fails before first token.
10. Release lease in `finally`.
11. Write audit row to `llm_gateway_request_log`.

## Streaming
- Streaming chat uses SSE passthrough.
- Gateway preserves upstream `data:` chunk payloads and forwards `[DONE]`.
- TTFT and usage are extracted when present and written to audit logs.

## Compatibility Strategy
- Permissive passthrough for optional OpenAI request keys.
- Strict validation only for core routing/security requirements.
- Error responses use OpenAI envelope:
  - `{ "error": { "type", "message", "param", "code" } }`

## Redis Keys
- `llm:active:{instance_id}`
- `llm:lease:{request_id}`
- `ratelimit:rpm:{tenant_id}:{window}`
- `ratelimit:tpm:{tenant_id}:{window}`

## Security
- JWT secret and algorithm are configured via env vars.
- `tenant_id` JWT claim is authoritative for policy resolution.
- Missing/invalid token and missing `tenant_id` return OpenAI-style errors.
