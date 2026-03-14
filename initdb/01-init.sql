CREATE EXTENSION IF NOT EXISTS vector;

-- DB for Langfuse
CREATE DATABASE langfuse;
-- DB for your RAG service
CREATE DATABASE rag;
-- DB for llm-port backend (used by local uv-run dev server)
CREATE DATABASE llm_port_backend;
-- DB for llm gateway metadata/audit
CREATE DATABASE llm_api;
-- DB for MCP tool registry
CREATE DATABASE llm_mcp;
-- DB for Skills registry
CREATE DATABASE llm_skills;

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
GRANT CONNECT ON DATABASE llm_mcp TO llm_user;
GRANT CONNECT ON DATABASE llm_skills TO llm_user;
GRANT CONNECT ON DATABASE llm_port_backend TO llm_port_backend;
GRANT ALL PRIVILEGES ON DATABASE llm_port_backend TO llm_port_backend;

-- Optional: enable pgvector in rag DB
\connect rag
CREATE EXTENSION IF NOT EXISTS vector;

-- Grant schema privileges for llm_port_backend (needed for Alembic migrations)
\connect llm_port_backend
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO llm_port_backend;

-- Allow llm_user (used by llm-port-api) to read the JWT secret from the backend DB at startup.
-- The API reads llm_port_api.jwt_secret from system_setting_secret on container start so it
-- stays in sync with System Settings without needing image rebuilds.
GRANT CONNECT ON DATABASE llm_port_backend TO llm_user;
GRANT USAGE ON SCHEMA public TO llm_user;
-- Grant SELECT on any table that llm_port_backend role creates (including post-migration tables).
ALTER DEFAULT PRIVILEGES FOR ROLE llm_port_backend IN SCHEMA public GRANT SELECT ON TABLES TO llm_user;
-- Also cover tables created by postgres (Alembic often runs as superuser).
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO llm_user;

-- Grant least privileges for gateway tables in llm_api
\connect llm_api
GRANT USAGE, CREATE ON SCHEMA public TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO llm_user;

-- Grant least privileges for MCP registry tables in llm_mcp
\connect llm_mcp
GRANT USAGE, CREATE ON SCHEMA public TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO llm_user;
