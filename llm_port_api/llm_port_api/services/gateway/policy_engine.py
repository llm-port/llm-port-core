"""Policy engine for tool execution governance.

Evaluates per-tool rules, tenant-level deny lists, sensitivity tiers,
and approval requirements before a tool call is dispatched to an executor.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


class PolicyAction(StrEnum):
    """Outcome of a policy evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class SensitivityTier(StrEnum):
    """Tool sensitivity classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    """Result of evaluating a single tool call against the policy engine."""

    action: PolicyAction
    reason: str | None = None
    required_approval_type: str | None = None


@dataclass(slots=True, frozen=True)
class ToolPolicyRule:
    """A single policy rule for a tool or tool pattern."""

    tool_pattern: str  # glob-style: "client.fs.*", "*", "mcp.github.create_issue"
    action: PolicyAction = PolicyAction.ALLOW
    sensitivity: SensitivityTier = SensitivityTier.LOW
    requires_approval: bool = False
    allowed_realms: set[str] = field(default_factory=set)
    argument_constraints: dict[str, Any] | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Default rules
# ---------------------------------------------------------------------------

_DEFAULT_RULES: list[ToolPolicyRule] = [
    # Server-managed and MCP tools allowed by default
    ToolPolicyRule(tool_pattern="server.*", action=PolicyAction.ALLOW, sensitivity=SensitivityTier.LOW),
    ToolPolicyRule(tool_pattern="mcp.*", action=PolicyAction.ALLOW, sensitivity=SensitivityTier.LOW),

    # Client filesystem tools require approval
    ToolPolicyRule(
        tool_pattern="client.fs.*",
        action=PolicyAction.ALLOW,
        sensitivity=SensitivityTier.MEDIUM,
        requires_approval=True,
        reason="Filesystem access requires user approval.",
    ),

    # Client command execution is high sensitivity
    ToolPolicyRule(
        tool_pattern="client.desktop.run_command",
        action=PolicyAction.ALLOW,
        sensitivity=SensitivityTier.CRITICAL,
        requires_approval=True,
        reason="Command execution requires explicit approval.",
    ),

    # Browser form submission requires approval
    ToolPolicyRule(
        tool_pattern="client.browser.submit_form",
        action=PolicyAction.ALLOW,
        sensitivity=SensitivityTier.HIGH,
        requires_approval=True,
        reason="Form submission requires user approval.",
    ),

    # Default: allow everything else
    ToolPolicyRule(tool_pattern="*", action=PolicyAction.ALLOW, sensitivity=SensitivityTier.LOW),
]


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


def _matches_pattern(tool_id: str, pattern: str) -> bool:
    """Simple glob-style pattern matching for tool IDs."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return tool_id == prefix or tool_id.startswith(prefix + ".")
    return tool_id == pattern


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """Evaluates tool calls against a rule set.

    Rules are evaluated in order; first match wins.
    Tenant-level overrides can be injected at construction time.
    """

    def __init__(
        self,
        *,
        rules: list[ToolPolicyRule] | None = None,
        tenant_deny_list: set[str] | None = None,
        auto_approve_low: bool = True,
    ) -> None:
        self._rules = rules or list(_DEFAULT_RULES)
        self._tenant_deny_list = tenant_deny_list or set()
        self._auto_approve_low = auto_approve_low

    async def evaluate(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        session_id: uuid.UUID,
        tenant_id: str,
        realm: str,
    ) -> PolicyDecision:
        """Evaluate a tool call against the policy rule set."""
        # 1. Tenant deny list
        if tool_id in self._tenant_deny_list:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=f"Tool '{tool_id}' is on the tenant deny list.",
            )

        # 2. Check deny-list patterns
        for pattern in self._tenant_deny_list:
            if _matches_pattern(tool_id, pattern):
                return PolicyDecision(
                    action=PolicyAction.DENY,
                    reason=f"Tool '{tool_id}' matches deny pattern '{pattern}'.",
                )

        # 3. Evaluate rules (first match wins)
        for rule in self._rules:
            if not _matches_pattern(tool_id, rule.tool_pattern):
                continue

            if rule.action == PolicyAction.DENY:
                return PolicyDecision(
                    action=PolicyAction.DENY,
                    reason=rule.reason or f"Denied by rule: {rule.tool_pattern}",
                )

            if rule.requires_approval:
                return PolicyDecision(
                    action=PolicyAction.APPROVAL_REQUIRED,
                    reason=rule.reason or f"Requires approval: {rule.tool_pattern}",
                    required_approval_type=rule.sensitivity.value,
                )

            # Argument constraints (basic key-value check)
            if rule.argument_constraints:
                for key, allowed_values in rule.argument_constraints.items():
                    actual = arguments.get(key)
                    if isinstance(allowed_values, list) and actual not in allowed_values:
                        return PolicyDecision(
                            action=PolicyAction.DENY,
                            reason=f"Argument '{key}' value not allowed.",
                        )

            return PolicyDecision(action=PolicyAction.ALLOW)

        # 4. Default: allow
        return PolicyDecision(action=PolicyAction.ALLOW)

    def add_rule(self, rule: ToolPolicyRule, *, prepend: bool = True) -> None:
        """Add a rule dynamically. Prepend to take priority."""
        if prepend:
            self._rules.insert(0, rule)
        else:
            self._rules.append(rule)

    def add_deny(self, tool_pattern: str, reason: str | None = None) -> None:
        """Convenience: add a deny rule for a tool pattern."""
        self._rules.insert(0, ToolPolicyRule(
            tool_pattern=tool_pattern,
            action=PolicyAction.DENY,
            reason=reason or f"Denied: {tool_pattern}",
        ))
