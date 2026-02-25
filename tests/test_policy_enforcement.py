"""Unit tests for the policy enforcement matrix (§5.2 of features/containers.md)."""

import pytest

from llm_port_backend.db.models.containers import ContainerClass, ContainerPolicy
from llm_port_backend.services.policy.enforcement import Action, PolicyEnforcer


@pytest.fixture()
def enforcer() -> PolicyEnforcer:
    return PolicyEnforcer()


# ──────────────────────────────────────────────────────────────────────────────
# TENANT_APP: admin can do everything except prune images
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action",
    [
        Action.VIEW,
        Action.LOGS_VIEW,
        Action.START,
        Action.STOP,
        Action.RESTART,
        Action.PAUSE,
        Action.UNPAUSE,
        Action.EXEC,
        Action.UPDATE_CONFIG,
        Action.DELETE,
        Action.IMAGE_PULL,
        Action.STACK_DEPLOY,
        Action.STACK_UPDATE,
        Action.STACK_ROLLBACK,
    ],
)
def test_tenant_app_admin_allowed(enforcer: PolicyEnforcer, action: Action) -> None:
    assert enforcer.check(action, ContainerClass.TENANT_APP, ContainerPolicy.FREE, root_mode_active=False)


def test_tenant_app_image_prune_denied_without_root(enforcer: PolicyEnforcer) -> None:
    assert not enforcer.check(Action.IMAGE_PRUNE, ContainerClass.TENANT_APP, ContainerPolicy.FREE, False)


def test_tenant_app_image_prune_allowed_with_root(enforcer: PolicyEnforcer) -> None:
    assert enforcer.check(Action.IMAGE_PRUNE, ContainerClass.TENANT_APP, ContainerPolicy.FREE, True)


# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM_CORE: admin can only view, start, restart
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action", [Action.VIEW, Action.LOGS_VIEW, Action.START, Action.RESTART])
def test_system_core_safe_allowed_admin(enforcer: PolicyEnforcer, action: Action) -> None:
    assert enforcer.check(action, ContainerClass.SYSTEM_CORE, ContainerPolicy.FREE, False)


@pytest.mark.parametrize(
    "action",
    [Action.STOP, Action.PAUSE, Action.EXEC, Action.DELETE, Action.UPDATE_CONFIG],
)
def test_system_core_destructive_denied_admin(enforcer: PolicyEnforcer, action: Action) -> None:
    assert not enforcer.check(action, ContainerClass.SYSTEM_CORE, ContainerPolicy.FREE, False)


@pytest.mark.parametrize(
    "action",
    [Action.STOP, Action.PAUSE, Action.EXEC, Action.DELETE, Action.UPDATE_CONFIG],
)
def test_system_core_all_allowed_root_mode(enforcer: PolicyEnforcer, action: Action) -> None:
    assert enforcer.check(action, ContainerClass.SYSTEM_CORE, ContainerPolicy.FREE, True)


# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM_AUX: view+read+start/restart allowed; destructive denied without root
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action", [Action.VIEW, Action.LOGS_VIEW, Action.START, Action.RESTART])
def test_system_aux_safe_admin(enforcer: PolicyEnforcer, action: Action) -> None:
    assert enforcer.check(action, ContainerClass.SYSTEM_AUX, ContainerPolicy.FREE, False)


@pytest.mark.parametrize("action", [Action.EXEC, Action.DELETE, Action.PAUSE])
def test_system_aux_destructive_denied_admin(enforcer: PolicyEnforcer, action: Action) -> None:
    assert not enforcer.check(action, ContainerClass.SYSTEM_AUX, ContainerPolicy.FREE, False)


# ──────────────────────────────────────────────────────────────────────────────
# LOCKED policy: blocks dangerous ops even in root mode
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action",
    [Action.STOP, Action.PAUSE, Action.EXEC, Action.DELETE, Action.UPDATE_CONFIG],
)
def test_locked_policy_blocks_even_root_mode(enforcer: PolicyEnforcer, action: Action) -> None:
    assert not enforcer.check(action, ContainerClass.SYSTEM_CORE, ContainerPolicy.LOCKED, True)


def test_locked_policy_allows_view_in_root_mode(enforcer: PolicyEnforcer) -> None:
    assert enforcer.check(Action.VIEW, ContainerClass.SYSTEM_CORE, ContainerPolicy.LOCKED, True)


# ──────────────────────────────────────────────────────────────────────────────
# UNTRUSTED: only VIEW allowed; everything else denied regardless of root mode
# ──────────────────────────────────────────────────────────────────────────────


def test_untrusted_view_allowed(enforcer: PolicyEnforcer) -> None:
    assert enforcer.check(Action.VIEW, ContainerClass.UNTRUSTED, ContainerPolicy.FREE, False)


def test_untrusted_logs_allowed(enforcer: PolicyEnforcer) -> None:
    assert enforcer.check(Action.LOGS_VIEW, ContainerClass.UNTRUSTED, ContainerPolicy.FREE, False)


@pytest.mark.parametrize("action", [Action.START, Action.EXEC])
def test_untrusted_action_denied(enforcer: PolicyEnforcer, action: Action) -> None:
    assert not enforcer.check(action, ContainerClass.UNTRUSTED, ContainerPolicy.FREE, False)


# ──────────────────────────────────────────────────────────────────────────────
# enforce() raises HTTPException on deny
# ──────────────────────────────────────────────────────────────────────────────


def test_enforce_raises_on_deny(enforcer: PolicyEnforcer) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        enforcer.enforce(Action.EXEC, ContainerClass.SYSTEM_CORE, ContainerPolicy.FREE, False)
    assert exc_info.value.status_code == 403
