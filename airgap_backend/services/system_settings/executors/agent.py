"""Remote agent executor contract placeholder."""

from __future__ import annotations

from airgap_backend.services.system_settings.executors.base import ApplyAction, ApplyExecutor


class AgentApplyExecutor(ApplyExecutor):
    """Contract-ready remote executor.

    v1 keeps local execution as default; remote execution is feature-gated.
    """

    async def execute(self, action: ApplyAction, target_host: str) -> list[str]:
        """Raise until remote agent transport is enabled."""
        msg = "Remote agent execution is not enabled in this environment."
        raise NotImplementedError(msg)
