# llm-port-backend

This project was generated using fastapi_template.

## UV

This project uses uv. It's a modern dependency management
tool.

To run the project use this set of commands:

```bash
uv sync --locked
uv run -m llm_port_backend
```

This will start the server on the configured host.

You can find swagger documentation at `/api/docs`.

You can read more about uv here: https://docs.astral.sh/ruff/

## Docker

You can start the project with docker using this command:

```bash
docker-compose up --build
```

If you want to develop in docker with autoreload and exposed ports add `-f deploy/docker-compose.dev.yml` to your docker command.
Like this:

```bash
docker-compose -f docker-compose.yml -f deploy/docker-compose.dev.yml --project-directory . up --build
```

This command exposes the web application on port 8000, mounts current directory and enables autoreload.

But you have to rebuild image every time you modify `uv.lock` or `pyproject.toml` with this command:

```bash
docker-compose build
```

## Project structure

```bash
$ tree "llm_port_backend"
llm_port_backend
├── conftest.py  # Fixtures for all tests.
├── db  # module contains db configurations
│   ├── dao  # Data Access Objects. Contains different classes to interact with database.
│   └── models  # Package contains different models for ORMs.
├── __main__.py  # Startup script. Starts uvicorn.
├── services  # Package for different external services such as rabbit or redis etc.
├── settings.py  # Main configuration settings for project.
├── static  # Static content.
├── tests  # Tests for project.
└── web  # Package contains web server. Handlers, startup config.
    ├── api  # Package with all handlers.
    │   └── router.py  # Main router.
    ├── application.py  # FastAPI application configuration.
    └── lifespan.py  # Contains actions to perform on startup and shutdown.
```

## Configuration

This application can be configured with environment variables.

You can create `.env` file in the root directory and place all
environment variables here. 

All environment variables should start with "LLM_PORT_BACKEND_" prefix.

For example if you see in your "llm_port_backend/settings.py" a variable named like
`random_parameter`, you should provide the "LLM_PORT_BACKEND_RANDOM_PARAMETER" 
variable to configure the value. This behaviour can be changed by overriding `env_prefix` property
in `llm_port_backend.settings.Settings.Config`.

An example of .env file:
```bash
LLM_PORT_BACKEND_RELOAD="True"
LLM_PORT_BACKEND_PORT="8000"
LLM_PORT_BACKEND_ENVIRONMENT="dev"
LLM_PORT_GRAFANA_URL="http://localhost:3001"
LLM_PORT_GRAFANA_DASHBOARD_UID_OVERVIEW=""
LLM_PORT_GRAFANA_PANELS_OVERVIEW=""
LLM_PORT_BACKEND_LOKI_BASE_URL="http://loki:3100"
LLM_PORT_BACKEND_LOGS_MAX_LIMIT="5000"
LLM_PORT_BACKEND_LOGS_DEFAULT_LIMIT="200"
LLM_PORT_BACKEND_LOGS_ALLOWED_LABELS="compose_service,container,job,host,level"
LLM_PORT_BACKEND_I18N_DIR="i18n"
```

You can read more about BaseSettings class here: https://pydantic-docs.helpmanual.io/usage/settings/

## Breaking Rename Migration (`llm-port` -> `llm-port`)

This release switches backend configuration names to `LLM_PORT_BACKEND_*` and removes support for the old
`llm_port_*` variable namespace.

Required migration actions:
1. Rename all backend env vars from `llm_port_backend_*` to `LLM_PORT_BACKEND_*`.
2. Rename Grafana env vars from `llm_port_GRAFANA_*` to `LLM_PORT_GRAFANA_*`.
3. Recreate containers/volumes if you rely on old docker identifiers (`llm_port_backend-*`).
4. Redeploy backend and worker together to avoid mixed env namespace usage.

## Logs API (Loki Proxy)

The frontend should never call Loki directly. All log traffic flows through this backend:

- `GET /api/logs/labels`
- `GET /api/logs/label/{name}/values`
- `GET /api/logs/query_range`
- `WebSocket /api/logs/tail?query=...`

All `/api/logs/*` endpoints require RBAC permission `logs:read` (superusers bypass checks).

## LLM Graph API

The backend now exposes graph data for the admin React Flow visualizer:

- `GET /api/llm/graph/topology`
- `GET /api/llm/graph/traces`
- `GET /api/llm/graph/traces/stream` (SSE)

All endpoints require RBAC permission `llm.graph:read` (superusers bypass checks).

The traces endpoints read from the gateway (`llm_port_api`) request log DB:

- `LLM_PORT_BACKEND_LLM_GRAPH_DB_HOST`
- `LLM_PORT_BACKEND_LLM_GRAPH_DB_PORT`
- `LLM_PORT_BACKEND_LLM_GRAPH_DB_USER`
- `LLM_PORT_BACKEND_LLM_GRAPH_DB_PASS`
- `LLM_PORT_BACKEND_LLM_GRAPH_DB_BASE`
- `LLM_PORT_BACKEND_LLM_GRAPH_DB_URL_OVERRIDE` (optional full DSN override)

## System Settings + Init Wizard APIs

The backend now exposes a system control plane used by `/admin/settings`:

- `GET /api/admin/system/settings/schema`
- `GET /api/admin/system/settings/values`
- `PUT /api/admin/system/settings/values/{key}`
- `GET /api/admin/system/apply/{job_id}`
- `GET /api/admin/system/wizard/steps`
- `POST /api/admin/system/wizard/apply`
- `POST /api/admin/system/agents/register`
- `POST /api/admin/system/agents/heartbeat`
- `GET /api/admin/system/agents`

Related env variables:
- `LLM_PORT_BACKEND_SETTINGS_MASTER_KEY`
- `LLM_PORT_BACKEND_SYSTEM_COMPOSE_FILE`
- `LLM_PORT_BACKEND_SYSTEM_AGENT_ENABLED`
- `LLM_PORT_BACKEND_SYSTEM_AGENT_TOKEN` (optional)

Docs:
- `docs/system-settings.md`
- `docs/system-init-wizard.md`
- `docs/agent-api.md`

## I18n API (runtime translation bundles)

The frontend loads translations at runtime from backend endpoints:

- `GET /api/i18n/languages`
- `GET /api/i18n/{lang}/{namespace}`

By default the backend reads bundles from `llm_port_backend/i18n` (configurable via `LLM_PORT_BACKEND_I18N_DIR`).

To add a language without frontend recompilation:
1. Create `llm_port_backend/i18n/<lang>/common.json`.
2. Refresh the frontend; the new language appears in the language selector.
## OpenTelemetry 

If you want to start your project with OpenTelemetry collector 
you can add `-f ./deploy/docker-compose.otlp.yml` to your docker command.

Like this:

```bash
docker-compose -f docker-compose.yml -f deploy/docker-compose.otlp.yml --project-directory . up
```

This command will start OpenTelemetry collector and jaeger. 
After sending a requests you can see traces in jaeger's UI
at http://localhost:16686/.

This docker configuration is not supposed to be used in production. 
It's only for demo purpose.

You can read more about OpenTelemetry here: https://opentelemetry.io/

## Pre-commit

To install pre-commit simply run inside the shell:
```bash
pre-commit install
```

pre-commit is very useful to check your code before publishing it.
It's configured using .pre-commit-config.yaml file.

By default it runs:
* mypy (validates types);
* ruff (spots possible bugs);


You can read more about pre-commit here: https://pre-commit.com/

## Migrations

If you want to migrate your database, you should run following commands:
```bash
# To run all migrations until the migration with revision_id.
alembic upgrade "<revision_id>"

# To perform all pending migrations.
alembic upgrade "head"
```

### Reverting migrations

If you want to revert migrations, you should run:
```bash
# revert all migrations up to: revision_id.
alembic downgrade <revision_id>

# Revert everything.
 alembic downgrade base
```

### Migration generation

To generate migrations you should run:
```bash
# For automatic change detection.
alembic revision --autogenerate

# For empty file generation.
alembic revision
```


## Running tests

If you want to run it in docker, simply run:

```bash
docker-compose run --build --rm api pytest -vv .
docker-compose down
```

For running tests on your local machine.
1. you need to start a database.

I prefer doing it with docker:
```
docker run -p "5432:5432" -e "POSTGRES_PASSWORD=llm_port_backend" -e "POSTGRES_USER=llm_port_backend" -e "POSTGRES_DB=llm_port_backend" postgres:18.1-bookworm
```


2. Run the pytest.
```bash
pytest -vv .
```

