# Database Setup (MVP)

## Recommended Postgres Setup
Use one shared Postgres server, with a dedicated gateway database and least-privileged user.

1. Create database:
```sql
CREATE DATABASE llm_api;
```
2. Create user:
```sql
CREATE USER llm_user WITH PASSWORD 'change-me';
```
3. Grant minimal access:
```sql
GRANT CONNECT ON DATABASE llm_api TO llm_user;
\c llm_api
GRANT USAGE ON SCHEMA public TO llm_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO llm_user;
```

## Tables
- `llm_model_alias`
- `llm_provider_instance`
- `llm_pool_membership`
- `tenant_llm_policy`
- `llm_gateway_request_log`

## Migrations
Run from `llm_port_api` root:
```bash
alembic upgrade head
```

Rollback:
```bash
alembic downgrade base
```

## Notes
- This service stores only gateway metadata and audit logs.
- Do not grant `llm_user` permissions on unrelated databases.
- `tenant_id` is derived from JWT claims and matched to `tenant_llm_policy.tenant_id`.
