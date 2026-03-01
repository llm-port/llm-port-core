FROM python:3.11.4-slim-bullseye AS prod
RUN apt-get update && apt-get install -y \
  gcc \
  && rm -rf /var/lib/apt/lists/*


RUN pip install poetry==1.8.2

# Configuring poetry
RUN poetry config virtualenvs.create false
RUN poetry config cache-dir /tmp/poetry_cache

# Copying requirements of a project
COPY pyproject.toml poetry.lock /app/src/
WORKDIR /app/src

# Installing requirements
RUN --mount=type=cache,target=/tmp/poetry_cache poetry install --only main

# Download spaCy English model for Presidio NER
RUN python -m spacy download en_core_web_lg

# Removing gcc
RUN apt-get purge -y \
  gcc \
  && rm -rf /var/lib/apt/lists/*

# Copying actuall application
COPY . /app/src/
RUN --mount=type=cache,target=/tmp/poetry_cache poetry install --only main

CMD ["/usr/local/bin/python", "-m", "llm_port_pii"]

FROM prod AS dev

RUN --mount=type=cache,target=/tmp/poetry_cache poetry install
