CREATE EXTENSION IF NOT EXISTS vector;

-- DB for Langfuse
CREATE DATABASE langfuse;
-- DB for your RAG service
CREATE DATABASE rag;
-- DB for llm-port backend (used by local uv-run dev server)
CREATE DATABASE llm_port_backend;
-- DB for llm gateway metadata/audit
CREATE DATABASE llm_api;
-- DB for PII service (only used when PII profile is enabled)
CREATE DATABASE pii;

-- Dedicated least-privileged role for llm_api
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'llm_user') THEN
        CREATE ROLE llm_user LOGIN PASSWORD 'llm_user';
    END IF;
END
$$;

-- Role for llm_port_backend (local dev server)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'llm_port_backend') THEN
        CREATE ROLE llm_port_backend LOGIN PASSWORD 'llm_port_backend';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE llm_api TO llm_user;
GRANT CONNECT ON DATABASE llm_port_backend TO llm_port_backend;
GRANT ALL PRIVILEGES ON DATABASE llm_port_backend TO llm_port_backend;

-- Optional: enable pgvector in rag DB
\connect rag
CREATE EXTENSION IF NOT EXISTS vector;

-- Grant schema privileges for llm_port_backend (needed for Alembic migrations)
\connect llm_port_backend
GRANT ALL ON SCHEMA public TO llm_port_backend;

-- Grant least privileges for gateway tables in llm_api
\connect llm_api
GRANT USAGE ON SCHEMA public TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO llm_user;
