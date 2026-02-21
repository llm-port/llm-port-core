"""Server-side policy enforcement for container actions.

The authorization matrix is derived directly from features/containers.md §5.2.
"""

from __future__ import annotations

import enum

from fastapi import HTTPException
from starlette import status

from airgap_backend.db.models.containers import ContainerClass, ContainerPolicy


class Action(enum.StrEnum):
    """Canonical set of actions that can be checked against policy."""

    # Container lifecycle / inspection
    VIEW = "container.view"
    LOGS_VIEW = "container.logs.view"
    START = "container.start"
    STOP = "container.stop"
    RESTART = "container.restart"
    PAUSE = "container.pause"
    UNPAUSE = "container.unpause"
    EXEC = "container.exec"
    UPDATE_CONFIG = "container.update_config"
    DELETE = "container.delete"
    # Image management
    IMAGE_PULL = "image.pull"
    IMAGE_PRUNE = "image.prune"
    # Stack management
    STACK_DEPLOY = "stack.deploy"
    STACK_UPDATE = "stack.update"
    STACK_ROLLBACK = "stack.rollback"


class PolicyError(Exception):
    """Raised when an action is denied by policy."""

    def __init__(self, action: Action, container_class: ContainerClass) -> None:
        super().__init__(f"Action '{action.value}' denied for container class '{container_class.value}'")
        self.action = action
        self.container_class = container_class


# ---------------------------------------------------------------------------
# Matrix encoding  (True = allowed by default for admin without root mode)
# ---------------------------------------------------------------------------
# (container_class, action) → allowed_for_admin
_ADMIN_ALLOW: dict[tuple[ContainerClass, Action], bool] = {
    # TENANT_APP — most actions allowed
    (ContainerClass.TENANT_APP, Action.VIEW): True,
    (ContainerClass.TENANT_APP, Action.LOGS_VIEW): True,
    (ContainerClass.TENANT_APP, Action.START): True,
    (ContainerClass.TENANT_APP, Action.STOP): True,
    (ContainerClass.TENANT_APP, Action.RESTART): True,
    (ContainerClass.TENANT_APP, Action.PAUSE): True,
    (ContainerClass.TENANT_APP, Action.UNPAUSE): True,
    (ContainerClass.TENANT_APP, Action.EXEC): True,
    (ContainerClass.TENANT_APP, Action.UPDATE_CONFIG): True,
    (ContainerClass.TENANT_APP, Action.DELETE): True,
    (ContainerClass.TENANT_APP, Action.IMAGE_PULL): True,
    (ContainerClass.TENANT_APP, Action.IMAGE_PRUNE): False,
    (ContainerClass.TENANT_APP, Action.STACK_DEPLOY): True,
    (ContainerClass.TENANT_APP, Action.STACK_UPDATE): True,
    (ContainerClass.TENANT_APP, Action.STACK_ROLLBACK): True,
    # SYSTEM_AUX — read + safe lifecycle; destructive ops denied
    (ContainerClass.SYSTEM_AUX, Action.VIEW): True,
    (ContainerClass.SYSTEM_AUX, Action.LOGS_VIEW): True,
    (ContainerClass.SYSTEM_AUX, Action.START): True,
    (ContainerClass.SYSTEM_AUX, Action.STOP): True,  # policy-based
    (ContainerClass.SYSTEM_AUX, Action.RESTART): True,
    (ContainerClass.SYSTEM_AUX, Action.PAUSE): False,
    (ContainerClass.SYSTEM_AUX, Action.UNPAUSE): False,
    (ContainerClass.SYSTEM_AUX, Action.EXEC): False,
    (ContainerClass.SYSTEM_AUX, Action.UPDATE_CONFIG): False,
    (ContainerClass.SYSTEM_AUX, Action.DELETE): False,
    (ContainerClass.SYSTEM_AUX, Action.IMAGE_PULL): True,
    (ContainerClass.SYSTEM_AUX, Action.IMAGE_PRUNE): False,
    (ContainerClass.SYSTEM_AUX, Action.STACK_DEPLOY): False,
    (ContainerClass.SYSTEM_AUX, Action.STACK_UPDATE): False,
    (ContainerClass.SYSTEM_AUX, Action.STACK_ROLLBACK): False,
    # SYSTEM_CORE — view + start/restart only; stop denied
    (ContainerClass.SYSTEM_CORE, Action.VIEW): True,
    (ContainerClass.SYSTEM_CORE, Action.LOGS_VIEW): True,
    (ContainerClass.SYSTEM_CORE, Action.START): True,
    (ContainerClass.SYSTEM_CORE, Action.STOP): False,  # stop denied
    (ContainerClass.SYSTEM_CORE, Action.RESTART): True,
    (ContainerClass.SYSTEM_CORE, Action.PAUSE): False,
    (ContainerClass.SYSTEM_CORE, Action.UNPAUSE): False,
    (ContainerClass.SYSTEM_CORE, Action.EXEC): False,
    (ContainerClass.SYSTEM_CORE, Action.UPDATE_CONFIG): False,
    (ContainerClass.SYSTEM_CORE, Action.DELETE): False,
    (ContainerClass.SYSTEM_CORE, Action.IMAGE_PULL): True,
    (ContainerClass.SYSTEM_CORE, Action.IMAGE_PRUNE): False,
    (ContainerClass.SYSTEM_CORE, Action.STACK_DEPLOY): False,
    (ContainerClass.SYSTEM_CORE, Action.STACK_UPDATE): False,
    (ContainerClass.SYSTEM_CORE, Action.STACK_ROLLBACK): False,
    # UNTRUSTED — view + logs only; everything else denied
    (ContainerClass.UNTRUSTED, Action.VIEW): True,
    (ContainerClass.UNTRUSTED, Action.LOGS_VIEW): True,
    (ContainerClass.UNTRUSTED, Action.START): False,
    (ContainerClass.UNTRUSTED, Action.STOP): False,
    (ContainerClass.UNTRUSTED, Action.RESTART): False,
    (ContainerClass.UNTRUSTED, Action.PAUSE): False,
    (ContainerClass.UNTRUSTED, Action.UNPAUSE): False,
    (ContainerClass.UNTRUSTED, Action.EXEC): False,
    (ContainerClass.UNTRUSTED, Action.UPDATE_CONFIG): False,
    (ContainerClass.UNTRUSTED, Action.DELETE): False,
    (ContainerClass.UNTRUSTED, Action.IMAGE_PULL): False,
    (ContainerClass.UNTRUSTED, Action.IMAGE_PRUNE): False,
    (ContainerClass.UNTRUSTED, Action.STACK_DEPLOY): False,
    (ContainerClass.UNTRUSTED, Action.STACK_UPDATE): False,
    (ContainerClass.UNTRUSTED, Action.STACK_ROLLBACK): False,
}

# Root mode: all actions allowed on any class
_ROOT_ALLOW_ALL = True

# Policy-overrides: LOCKED containers block everything even for root mode
# (safety net for the immutable desired-state spec)
_LOCKED_DENY_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.STOP,
        Action.PAUSE,
        Action.EXEC,
        Action.UPDATE_CONFIG,
        Action.DELETE,
    }
)


class PolicyEnforcer:
    """Stateless policy checker; inject as a dependency where needed."""

    def check(
        self,
        action: Action,
        container_class: ContainerClass,
        policy: ContainerPolicy,
        root_mode_active: bool,
    ) -> bool:
        """
        Return True if the action is permitted, False otherwise.

        :param action: the action being attempted.
        :param container_class: class of the target container from registry.
        :param policy: policy level of the container.
        :param root_mode_active: whether the current user has an active root session.
        """
        # LOCKED policy blocks dangerous ops even in root mode
        if policy == ContainerPolicy.LOCKED and action in _LOCKED_DENY_ACTIONS:
            return False

        # Root mode: allow all remaining actions
        if root_mode_active:
            return True

        # Default matrix lookup
        return _ADMIN_ALLOW.get((container_class, action), False)

    def enforce(
        self,
        action: Action,
        container_class: ContainerClass,
        policy: ContainerPolicy,
        root_mode_active: bool,
    ) -> None:
        """
        Raise :class:`HTTPException` (403) if the action is denied.

        :raises HTTPException: if the action is not permitted.
        """
        if not self.check(action, container_class, policy, root_mode_active):
            detail = f"Action '{action.value}' is not permitted on {container_class.value} containers"
            if not root_mode_active and container_class in (
                ContainerClass.SYSTEM_CORE,
                ContainerClass.SYSTEM_AUX,
            ):
                detail += ". Activate Root Mode for elevated access."
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def get_policy_enforcer() -> PolicyEnforcer:
    """FastAPI dependency factory for the policy enforcer."""
    return PolicyEnforcer()
