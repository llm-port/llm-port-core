ARG BASE_IMAGE=ghcr.io/llm-port/base:latest
FROM ${BASE_IMAGE} AS prod

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --inexact --no-install-project --no-dev

COPY . .

# The runtime manifests pin the images clusters run. The runtime image's
# build writes them beside this directory (../llm_port_runtime_image), so
# they come in as a named build context -- compose's additional_contexts,
# CI's build-contexts. Without them the backend did not start: "runtime
# manifest 'runtime-manifest.json' not found".
COPY --from=runtime_manifests runtime-manifest*.json llm_port_runtime_image/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --inexact --no-dev

CMD ["/usr/local/bin/python", "-m", "llm_port_backend"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups
