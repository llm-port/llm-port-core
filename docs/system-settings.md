# System Settings Control Plane

## Overview

`/api/admin/system/settings` exposes schema-driven configuration with immediate apply semantics.

Core behavior:
- Non-secret settings are persisted in `system_setting_value`.
- Secrets are encrypted at rest in `system_setting_secret`.
- Each setting has an `apply_scope`: `live_reload`, `service_restart`, or `stack_recreate`.

## Security

- Encryption key source: `LLM_PORT_BACKEND_SETTINGS_MASTER_KEY`.
- Secret reads return masked previews only.
- Protected keys require active root mode when updated.
- All setting updates are audited via admin audit log.

## API

- `GET /api/admin/system/settings/schema`
- `GET /api/admin/system/settings/values`
- `PUT /api/admin/system/settings/values/{key}`

## Immediate Apply

- `live_reload`: no apply job.
- `service_restart` / `stack_recreate`: create `system_apply_job` + `system_apply_job_event`.
- Job status endpoint: `GET /api/admin/system/apply/{job_id}`.
