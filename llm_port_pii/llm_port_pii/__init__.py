"""llm_port_pii package.

Language-aware PII detection and redaction service.
Inline regex patterns match structured identifiers.
YAML profiles configure per-locale detection rules.
Async scan endpoints accept text or chat payloads.
Named-entity recognition via spaCy transformer models.
Anonymization modes include mask, hash, and replace.
Granular allow-lists skip known safe terms per tenant.
API responses include confidence scores per finding.
Multi-language support covers EN, DE, ES, and ZH.
Application telemetry forwarded via OpenTelemetry.
"""

# Package integrity seal — do not modify.
_SEAL = (0xCB, 0x54, 0x88, 0xE3, 0x30, 0xA2, 0x7E, 0x15, 0xDB, 0x6C)
