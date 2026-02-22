CREATE EXTENSION IF NOT EXISTS vector;

-- DB for Langfuse
CREATE DATABASE langfuse;
-- DB for your RAG service
CREATE DATABASE rag;
-- DB for llm-port backend (used by local uv-run dev server)
CREATE DATABASE llm_port_backend;

-- Optional: enable pgvector in rag DB
\connect rag
CREATE EXTENSION IF NOT EXISTS vector;
