"""Tests for PII policy parsing, validation, and clamping logic.

RED tests first — these prove the vulnerability (fail-open, no validation),
then GREEN implementation makes them pass.
"""

from __future__ import annotations

import pytest

from llm_port_api.services.gateway.pii_policy import (
    VALID_ENTITIES,
    PIIEgressPolicy,
    PIIPolicy,
    PIIPresidioConfig,
    PIITelemetryPolicy,
    SessionPIIOverride,
    clamp_and_merge,
    max_protective_action,
    parse_pii_policy,
)


# ── parse_pii_policy validation tests ────────────────────────────

VALID_MODES = {"redact", "tokenize_reversible"}
VALID_FAIL_ACTIONS = {"block", "allow", "fallback_to_local"}


class TestParsePiiPolicyValidation:
    """parse_pii_policy must reject or sanitize unknown enum values."""

    def test_unknown_fail_action_defaults_to_block(self) -> None:
        """Unknown fail_action values must default to 'block' (fail-closed)."""
        raw = {
            "egress": {
                "enabled_for_cloud": True,
                "fail_action": "yolo_send_it",
            },
        }
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.fail_action == "block"

    def test_unknown_mode_defaults_to_redact(self) -> None:
        """Unknown mode values must default to 'redact'."""
        raw = {
            "egress": {
                "enabled_for_cloud": True,
                "mode": "passthrough",
            },
        }
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.mode == "redact"

    def test_valid_mode_redact_accepted(self) -> None:
        raw = {"egress": {"enabled_for_cloud": True, "mode": "redact"}}
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.mode == "redact"

    def test_valid_mode_tokenize_accepted(self) -> None:
        raw = {"egress": {"enabled_for_cloud": True, "mode": "tokenize_reversible"}}
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.mode == "tokenize_reversible"

    def test_valid_fail_action_block_accepted(self) -> None:
        raw = {"egress": {"enabled_for_cloud": True, "fail_action": "block"}}
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.fail_action == "block"

    def test_valid_fail_action_allow_accepted(self) -> None:
        raw = {"egress": {"enabled_for_cloud": True, "fail_action": "allow"}}
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.fail_action == "allow"

    def test_valid_fail_action_fallback_accepted(self) -> None:
        raw = {
            "egress": {
                "enabled_for_cloud": True,
                "fail_action": "fallback_to_local",
            },
        }
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.egress.fail_action == "fallback_to_local"

    def test_unknown_entities_filtered_out(self) -> None:
        """Entity names not in the supported catalog must be stripped."""
        raw = {
            "egress": {"enabled_for_cloud": True},
            "presidio": {
                "entities": [
                    "EMAIL_ADDRESS",
                    "TOTALLY_FAKE_ENTITY",
                    "PERSON",
                    "NONEXISTENT_TYPE",
                ],
            },
        }
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert "EMAIL_ADDRESS" in policy.presidio.entities
        assert "PERSON" in policy.presidio.entities
        assert "TOTALLY_FAKE_ENTITY" not in policy.presidio.entities
        assert "NONEXISTENT_TYPE" not in policy.presidio.entities

    def test_threshold_clamped_to_valid_range(self) -> None:
        """Threshold outside [0.0, 1.0] must be clamped."""
        raw = {
            "egress": {"enabled_for_cloud": True},
            "presidio": {"threshold": 1.5},
        }
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert 0.0 <= policy.presidio.threshold <= 1.0

    def test_negative_threshold_clamped(self) -> None:
        raw = {
            "egress": {"enabled_for_cloud": True},
            "presidio": {"threshold": -0.3},
        }
        policy = parse_pii_policy(raw)
        assert policy is not None
        assert policy.presidio.threshold >= 0.0

    def test_none_raw_returns_none(self) -> None:
        assert parse_pii_policy(None) is None

    def test_empty_raw_returns_none(self) -> None:
        assert parse_pii_policy({}) is None

    def test_all_disabled_returns_none(self) -> None:
        raw = {
            "egress": {"enabled_for_cloud": False, "enabled_for_local": False},
            "telemetry": {"enabled": False},
        }
        assert parse_pii_policy(raw) is None


# ── Helpers ──────────────────────────────────────────────────────

def _floor() -> PIIPolicy:
    """A reasonable floor (tenant/system baseline)."""
    return PIIPolicy(
        telemetry=PIITelemetryPolicy(enabled=True, mode="sanitized", store_raw=False),
        egress=PIIEgressPolicy(
            enabled_for_cloud=True,
            enabled_for_local=False,
            mode="redact",
            fail_action="allow",
        ),
        presidio=PIIPresidioConfig(
            language="en",
            threshold=0.5,
            entities=["EMAIL_ADDRESS", "PHONE_NUMBER"],
        ),
    )


def _empty_override() -> SessionPIIOverride:
    """Override with all None — inherits everything."""
    return SessionPIIOverride()


# ── max_protective_action tests ──────────────────────────────────

class TestMaxProtectiveAction:
    def test_block_beats_allow(self) -> None:
        assert max_protective_action("allow", "block") == "block"

    def test_block_beats_fallback(self) -> None:
        assert max_protective_action("fallback_to_local", "block") == "block"

    def test_fallback_beats_allow(self) -> None:
        assert max_protective_action("allow", "fallback_to_local") == "fallback_to_local"

    def test_cannot_weaken_from_block_to_allow(self) -> None:
        assert max_protective_action("block", "allow") == "block"

    def test_cannot_weaken_from_fallback_to_allow(self) -> None:
        assert max_protective_action("fallback_to_local", "allow") == "fallback_to_local"

    def test_none_override_keeps_floor(self) -> None:
        assert max_protective_action("allow", None) == "allow"

    def test_unknown_override_defaults_to_block(self) -> None:
        assert max_protective_action("allow", "yolo") == "block"

    def test_unknown_floor_defaults_to_block(self) -> None:
        assert max_protective_action("yolo", "allow") == "block"


# ── clamp_and_merge tests ────────────────────────────────────────

class TestClampAndMerge:
    # --- Passthrough / identity ---

    def test_empty_override_returns_floor(self) -> None:
        """All-None override should produce the floor unchanged."""
        floor = _floor()
        result = clamp_and_merge(floor, _empty_override())
        assert result.egress.mode == floor.egress.mode
        assert result.egress.fail_action == floor.egress.fail_action
        assert result.egress.enabled_for_cloud == floor.egress.enabled_for_cloud
        assert result.egress.enabled_for_local == floor.egress.enabled_for_local
        assert result.telemetry.enabled == floor.telemetry.enabled
        assert result.telemetry.mode == floor.telemetry.mode
        assert result.telemetry.store_raw == floor.telemetry.store_raw
        assert result.presidio.threshold == floor.presidio.threshold
        assert result.presidio.entities == floor.presidio.entities

    # --- egress.enabled booleans (OR logic) ---

    def test_cannot_disable_cloud_egress(self) -> None:
        """Session cannot turn off cloud egress if floor has it on."""
        floor = _floor()  # enabled_for_cloud=True
        override = SessionPIIOverride(egress_enabled_for_cloud=False)
        result = clamp_and_merge(floor, override)
        assert result.egress.enabled_for_cloud is True

    def test_can_enable_local_egress(self) -> None:
        """Session can turn on local egress even if floor has it off."""
        floor = _floor()  # enabled_for_local=False
        override = SessionPIIOverride(egress_enabled_for_local=True)
        result = clamp_and_merge(floor, override)
        assert result.egress.enabled_for_local is True

    # --- egress.mode ---

    def test_mode_override_blocked_by_default(self) -> None:
        """Mode switching requires allow_mode_override=True."""
        floor = _floor()  # mode="redact"
        override = SessionPIIOverride(egress_mode="tokenize_reversible")
        result = clamp_and_merge(floor, override, allow_mode_override=False)
        assert result.egress.mode == "redact"

    def test_mode_override_allowed_when_flag_set(self) -> None:
        """Mode switching works when allow_mode_override=True."""
        floor = _floor()
        override = SessionPIIOverride(egress_mode="tokenize_reversible")
        result = clamp_and_merge(floor, override, allow_mode_override=True)
        assert result.egress.mode == "tokenize_reversible"

    def test_mode_override_invalid_value_ignored(self) -> None:
        """Invalid mode string ignored even with allow_mode_override=True."""
        floor = _floor()
        override = SessionPIIOverride(egress_mode="passthrough")
        result = clamp_and_merge(floor, override, allow_mode_override=True)
        assert result.egress.mode == "redact"

    # --- egress.fail_action ---

    def test_strengthen_fail_action_allow_to_block(self) -> None:
        floor = _floor()  # fail_action="allow"
        override = SessionPIIOverride(egress_fail_action="block")
        result = clamp_and_merge(floor, override)
        assert result.egress.fail_action == "block"

    def test_cannot_weaken_fail_action_block_to_allow(self) -> None:
        floor = PIIPolicy(
            egress=PIIEgressPolicy(
                enabled_for_cloud=True,
                fail_action="block",
            ),
        )
        override = SessionPIIOverride(egress_fail_action="allow")
        result = clamp_and_merge(floor, override)
        assert result.egress.fail_action == "block"

    # --- telemetry ---

    def test_cannot_disable_telemetry(self) -> None:
        floor = _floor()  # telemetry.enabled=True
        override = SessionPIIOverride(telemetry_enabled=False)
        result = clamp_and_merge(floor, override)
        assert result.telemetry.enabled is True

    def test_can_enable_telemetry(self) -> None:
        floor = PIIPolicy(
            telemetry=PIITelemetryPolicy(enabled=False),
            egress=PIIEgressPolicy(enabled_for_cloud=True),
        )
        override = SessionPIIOverride(telemetry_enabled=True)
        result = clamp_and_merge(floor, override)
        assert result.telemetry.enabled is True

    def test_telemetry_mode_overridable(self) -> None:
        floor = _floor()  # telemetry.mode="sanitized"
        override = SessionPIIOverride(telemetry_mode="metrics_only")
        result = clamp_and_merge(floor, override)
        assert result.telemetry.mode == "metrics_only"

    def test_store_raw_not_overridable(self) -> None:
        """store_raw is a data-retention decision — not session-overridable."""
        floor = _floor()  # store_raw=False
        result = clamp_and_merge(floor, _empty_override())
        assert result.telemetry.store_raw is False

    # --- presidio.threshold ---

    def test_can_raise_threshold(self) -> None:
        floor = _floor()  # threshold=0.5
        override = SessionPIIOverride(presidio_threshold=0.8)
        result = clamp_and_merge(floor, override)
        assert result.presidio.threshold == 0.8

    def test_cannot_lower_threshold(self) -> None:
        floor = _floor()  # threshold=0.5
        override = SessionPIIOverride(presidio_threshold=0.2)
        result = clamp_and_merge(floor, override)
        assert result.presidio.threshold == 0.5

    # --- presidio.entities ---

    def test_can_add_valid_entity(self) -> None:
        floor = _floor()  # entities=["EMAIL_ADDRESS", "PHONE_NUMBER"]
        override = SessionPIIOverride(presidio_entities_add=["CREDIT_CARD"])
        result = clamp_and_merge(floor, override)
        assert "CREDIT_CARD" in result.presidio.entities
        assert "EMAIL_ADDRESS" in result.presidio.entities
        assert "PHONE_NUMBER" in result.presidio.entities

    def test_cannot_remove_floor_entities(self) -> None:
        """Floor entities always present regardless of override."""
        floor = _floor()
        override = SessionPIIOverride(presidio_entities_add=[])
        result = clamp_and_merge(floor, override)
        assert "EMAIL_ADDRESS" in result.presidio.entities
        assert "PHONE_NUMBER" in result.presidio.entities

    def test_invalid_entity_in_add_filtered(self) -> None:
        floor = _floor()
        override = SessionPIIOverride(
            presidio_entities_add=["CREDIT_CARD", "TOTALLY_FAKE"],
        )
        result = clamp_and_merge(floor, override)
        assert "CREDIT_CARD" in result.presidio.entities
        assert "TOTALLY_FAKE" not in result.presidio.entities


# ── _resolve_pii_policy integration tests ────────────────────────


class TestResolvePiiPolicyWithOverride:
    """Tests for _resolve_pii_policy accepting an optional session override row."""

    @staticmethod
    def _make_mock_policy(pii_config: dict | None = None):  # noqa: ANN205
        """Create a mock TenantLLMPolicy-like object."""

        class _Stub:
            pass

        stub = _Stub()
        stub.pii_config = pii_config  # type: ignore[attr-defined]
        return stub

    @staticmethod
    def _make_mock_override(**kwargs):  # noqa: ANN003, ANN205
        """Create a mock SessionPIIOverrideRow-like object."""

        class _Row:
            pii_enabled = None
            egress_enabled_for_cloud = None
            egress_enabled_for_local = None
            egress_mode = None
            egress_fail_action = None
            telemetry_enabled = None
            telemetry_mode = None
            presidio_threshold = None
            presidio_entities_add = None

        row = _Row()
        for k, v in kwargs.items():
            setattr(row, k, v)
        return row

    def test_no_override_returns_floor(self) -> None:
        from llm_port_api.services.gateway.service import _resolve_pii_policy

        policy = self._make_mock_policy({
            "egress": {"enabled_for_cloud": True, "fail_action": "allow"},
            "presidio": {"threshold": 0.5, "entities": ["EMAIL_ADDRESS"]},
        })
        result = _resolve_pii_policy(policy, session_override=None)
        assert result is not None
        assert result.egress.fail_action == "allow"

    def test_override_clamps_fail_action(self) -> None:
        from llm_port_api.services.gateway.service import _resolve_pii_policy

        policy = self._make_mock_policy({
            "egress": {"enabled_for_cloud": True, "fail_action": "allow"},
            "presidio": {"threshold": 0.5, "entities": ["EMAIL_ADDRESS"]},
        })
        override = self._make_mock_override(egress_fail_action="block")
        result = _resolve_pii_policy(policy, session_override=override)
        assert result is not None
        assert result.egress.fail_action == "block"

    def test_override_cannot_weaken_fail_action(self) -> None:
        from llm_port_api.services.gateway.service import _resolve_pii_policy

        policy = self._make_mock_policy({
            "egress": {"enabled_for_cloud": True, "fail_action": "block"},
            "presidio": {"threshold": 0.5, "entities": ["EMAIL_ADDRESS"]},
        })
        override = self._make_mock_override(egress_fail_action="allow")
        result = _resolve_pii_policy(policy, session_override=override)
        assert result is not None
        # Cannot weaken — floor block stays
        assert result.egress.fail_action == "block"

    def test_override_raises_threshold(self) -> None:
        from llm_port_api.services.gateway.service import _resolve_pii_policy

        policy = self._make_mock_policy({
            "egress": {"enabled_for_cloud": True, "fail_action": "allow"},
            "presidio": {"threshold": 0.5, "entities": ["EMAIL_ADDRESS"]},
        })
        override = self._make_mock_override(presidio_threshold=0.9)
        result = _resolve_pii_policy(policy, session_override=override)
        assert result is not None
        assert result.presidio.threshold == 0.9

    def test_no_pii_config_returns_none(self) -> None:
        from llm_port_api.services.gateway.service import _resolve_pii_policy

        policy = self._make_mock_policy(pii_config=None)
        override = self._make_mock_override(egress_fail_action="block")
        result = _resolve_pii_policy(policy, session_override=override)
        assert result is None
