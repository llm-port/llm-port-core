ARG BASE_IMAGE=llmport/base:latest
FROM ${BASE_IMAGE} AS prod

# Node.js runtime for npx-based MCP servers
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --inexact --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --inexact --no-dev

CMD ["/usr/local/bin/python", "-m", "llm_port_mcp"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups
