# llm_port_skills — Centralized Skills Registry

Skills Registry microservice for **llm.port**. Manages reusable reasoning playbooks (skills) that shape how the system reasons about classes of requests.

## What is a Skill?

A **Skill** is a reusable instruction pack (Markdown + YAML frontmatter) that sits at the orchestration layer between RAG context, MCP tools, and prompt composition.

- **MCP** = what the system can do (external tool calls)
- **RAG** = what the system knows (document retrieval)
- **Skills** = how the system should solve a task (reasoning playbooks)

## Quick Start

```bash
# Development
docker compose up -d

# Run migrations
alembic upgrade head

# Start service
python -m llm_port_skills
```

## Environment Variables

| Variable                        | Default      | Description                   |
| ------------------------------- | ------------ | ----------------------------- |
| `LLM_PORT_SKILLS_PORT`          | `8008`       | Service port                  |
| `LLM_PORT_SKILLS_DB_HOST`       | `127.0.0.1`  | PostgreSQL host               |
| `LLM_PORT_SKILLS_DB_BASE`       | `llm_skills` | Database name                 |
| `LLM_PORT_SKILLS_REDIS_HOST`    | ``           | Redis host (empty = disabled) |
| `LLM_PORT_SKILLS_SERVICE_TOKEN` | ``           | Internal service auth token   |
| `LLM_PORT_SKILLS_JWT_SECRET`    | ``           | JWT validation secret         |

## API

### Admin (JWT auth, proxied through backend)

- `GET /api/v1/skills/` — list skills
- `POST /api/v1/skills/` — create skill
- `GET /api/v1/skills/{id}` — get skill detail
- `PUT /api/v1/skills/{id}` — update metadata
- `PUT /api/v1/skills/{id}/body` — update body (new version)
- `POST /api/v1/skills/{id}/publish` — publish
- `POST /api/v1/skills/{id}/archive` — archive
- `DELETE /api/v1/skills/{id}` — delete
- `GET /api/v1/skills/{id}/versions` — list versions
- `POST /api/v1/skills/{id}/assign` — create assignment
- `POST /api/v1/skills/import` — import from .md file
- `GET /api/v1/skills/{id}/export` — export as .md
- `GET /api/v1/skills/completions` — editor intellisense data

### Internal (service token auth)

- `POST /api/internal/skills/resolve` — resolve skills for request
- `POST /api/internal/skills/usage` — record usage telemetry
