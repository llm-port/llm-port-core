# Feature: PII Protection Pipeline (Gateway + Separate `llm_port_pii` Service)

Status: **Proposed (MVP-ready)**  
Owners: `llm_port_api` (Gateway), `llm_port_pii` (PII Service)  
Primary goal: Prevent sensitive data from leaving the system unprotected **and** prevent accidental leakage via **Langfuse / logs / audit tables**.

---

## 1. Summary

`llm.port` adds an optional PII protection layer that can be enabled per **tenant/workspace**. When enabled:

1) **Telemetry Sanitization (recommended default)**  
All prompt/response content sent to **Langfuse** (and any DB audit tables containing text) is sanitized, **even for local LLM routes**.

2) **Egress Sanitization (for cloud routes)**  
If the chosen provider is a **cloud provider**, the request is sanitized before it is sent upstream.

PII detection + transformation runs in a separate internal FastAPI service: **`llm_port_pii`**, using **Microsoft Presidio**.

---

## 2. Goals

- Provide a **central** PII enforcement point in the Gateway pipeline.
- Support **two independent protections**:
  - **Egress protection** (before sending to cloud providers)
  - **Telemetry protection** (before sending to Langfuse/logs/audit DB)
- Keep the PII implementation **provider-agnostic** (OpenAI-compatible schema walker).
- Allow tenant/workspace policies to control:
  - enablement, modes, entity types, thresholds
  - behavior on failure (`block`, `fallback_to_local`, etc.)
- Keep `llm_port_pii` **internal-only** (no public exposure).

---

## 3. Non-goals (MVP)

- Token-by-token PII detection during SSE streaming (too expensive/fragile).
- Perfect language/entity accuracy across all locales.
- Full DLP or data classification beyond PII detection.
- Persisting raw prompts/responses for debugging (MVP defaults to not storing raw).

---

## 4. Architecture Overview

### 4.1 Components

- **Gateway** (`llm_port_api`)
  - Resolves tenant/workspace policy and route decision
  - Calls `llm_port_pii` for sanitization/transform
  - Ensures Langfuse + DB logs only receive sanitized/metrics-only data

- **PII Service** (`llm_port_pii`)
  - FastAPI wrapper around Presidio Analyzer + Anonymizer
  - Accepts OpenAI-shaped payloads, sanitizes text-bearing fields
  - Returns sanitized payload + PII report + optional mapping reference

- **Langfuse**
  - Receives sanitized content or metrics-only depending on policy

- **Optional Redis (Mapping Store)**
  - Only needed if reversible tokenization is enabled and mapping must outlive a single request.

### 4.2 Data Path Insert Points

**Telemetry Sanitization**:
- Always applied (if enabled) **before**:
  - `GW -> Langfuse`
  - `GW -> Postgres audit rows` (any text columns)
  - (optional) structured logs

**Egress Sanitization**:
- Applied (if enabled) **before**:
  - `GW -> Cloud Provider` (OpenAI-compatible upstream)

---

## 5. Policy Model

Policy is loaded by Gateway from Postgres (tenant/workspace scope).

### 5.1 Policy Fields (proposed)

```json
{
  "pii": {
    "telemetry": {
      "enabled": true,
      "mode": "sanitized",         // sanitized | metrics_only
      "store_raw": false           // MVP default: false (never store raw in LF/DB)
    },
    "egress": {
      "enabled_for_cloud": true,
      "enabled_for_local": false,  // optional strict mode
      "mode": "redact",            // redact | tokenize_reversible
      "fail_action": "fallback_to_local" // block | allow | fallback_to_local
    },
    "presidio": {
      "language": "en",
      "threshold": 0.6,
      "entities": [
        "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE",
        "PERSON", "LOCATION"
      ]
    }
  }
}
```

### 5.2 Recommended Defaults examples (naming can differ)

For privacy-heavy tenants:

- pii.telemetry.enabled = true
- pii.telemetry.mode = sanitized
- pii.egress.enabled_for_cloud = true
- pii.egress.fail_action = fallback_to_local (or block for strict orgs)
- pii.egress.enabled_for_local = false (enable only if required)