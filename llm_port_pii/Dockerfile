ARG BASE_IMAGE=ghcr.io/llm-port/base:latest
FROM ${BASE_IMAGE} AS prod

RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=uv.lock,target=uv.lock \
  --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
  uv sync --locked --inexact --no-install-project --no-dev

# Download spaCy English model for Presidio NER.
# Install via pip (uv pip) so the model lands in UV_PROJECT_ENVIRONMENT
# and benefits from the Docker layer cache, avoiding `spacy download`
# which shells out to pip in a way that can bypass the uv environment.
RUN uv pip install --system \
  https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --inexact --no-dev

CMD ["/usr/local/bin/python", "-m", "llm_port_pii"]

FROM prod AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --all-groups
