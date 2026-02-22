# System Initialization Wizard

## Purpose

The wizard provides step-based initialization while reusing the same settings update path as General Settings.

## Endpoints

- `GET /api/admin/system/wizard/steps`
- `POST /api/admin/system/wizard/apply`

## Steps

1. Host Target
2. Core Data Services
3. Auth and Shared Secrets
4. LLM Gateway Integration
5. Langfuse + Grafana/Loki/Alloy
6. Health Verification

## Execution Model

- Step apply calls settings updates directly.
- Any non-live setting triggers immediate apply behavior.
- Failures are returned per-setting and retained in apply job events.
