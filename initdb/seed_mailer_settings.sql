INSERT INTO system_setting_value (key, value_json, version)
VALUES
  ('llm_port_mailer.enabled', '{"value": false}', 1),
  ('llm_port_mailer.service_url', '{"value": "http://llm-port-mailer:8000"}', 1),
  ('llm_port_mailer.frontend_base_url', '{"value": "http://localhost:5173"}', 1),
  ('llm_port_mailer.admin_recipients', '{"value": []}', 1),
  ('llm_port_mailer.alert_5xx_threshold_percent', '{"value": 5}', 1),
  ('llm_port_mailer.alert_5xx_window_minutes', '{"value": 5}', 1),
  ('llm_port_mailer.alert_cooldown_minutes', '{"value": 30}', 1),
  ('llm_port_mailer.smtp.host', '{"value": ""}', 1),
  ('llm_port_mailer.smtp.port', '{"value": 587}', 1),
  ('llm_port_mailer.smtp.starttls', '{"value": true}', 1),
  ('llm_port_mailer.smtp.ssl', '{"value": false}', 1),
  ('llm_port_mailer.from_email', '{"value": "noreply@llm-port.local"}', 1),
  ('llm_port_mailer.from_name', '{"value": "LLM Port"}', 1)
ON CONFLICT (key)
DO UPDATE SET value_json = EXCLUDED.value_json, version = system_setting_value.version + 1;
