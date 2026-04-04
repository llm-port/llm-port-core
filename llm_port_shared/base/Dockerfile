# ── llm.port platform base image ──────────────────────────────────
#
# Pre-installs Python, uv, gcc, and the ~27 common packages shared
# across all llm.port microservices.
#
# Service Dockerfiles extend this with:
#   ARG BASE_IMAGE=ghcr.io/llm-port/base:latest
#   FROM ${BASE_IMAGE} AS prod
# ──────────────────────────────────────────────────────────────────

ARG UV_VERSION=0.9.12
ARG PYTHON_BASE=python:3.13-slim-bookworm

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${PYTHON_BASE}

# gcc is required by several services for native C extensions
# (asyncpg, cryptography, pydantic-core, uvloop, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Bake uv into the image so downstream Dockerfiles don't need the
# --mount=from=uv dance.
COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_MANAGED_PYTHON=1

WORKDIR /app/src

# Pre-install common platform packages.  Each service's `uv sync --locked`
# will adjust to its own lock file, but most packages will already be
# at the right version — making the per-service build layer very small.
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --compile-bytecode -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
