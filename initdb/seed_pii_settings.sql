INSERT INTO system_setting_value (key, value_json, version)
VALUES
  ('llm_port_api.pii_enabled', '{"value": true}', 1),
  ('llm_port_api.pii_service_url', '{"value": "http://llm-port-pii:8000"}', 1),
  ('llm_port_api.pii_default_policy', '{"value": {"egress": {"enabled_for_cloud": true, "enabled_for_local": false, "mode": "redact", "fail_action": "allow"}, "telemetry": {"enabled": false}, "presidio": {"language": "en", "threshold": 0.6, "entities": ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "PERSON", "LOCATION"]}}}', 1)
ON CONFLICT (key)
DO UPDATE SET value_json = EXCLUDED.value_json, version = system_setting_value.version + 1;
