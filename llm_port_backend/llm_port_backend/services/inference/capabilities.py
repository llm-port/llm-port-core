"""Environment capability model for the neutral inference domain.

Capabilities are environment/version-specific and are produced by drivers
(through ``InferenceDriver.capabilities``) or observed during
reconciliation.  The canonical document shape is intentionally small and
stable so the frontend can be capability-driven instead of branching on
``backend == <name>`` checks.

Example (Ray 2.58-class environment)::

    {
      "driver": "ray",
      "backend_version": "2.58.0",
      "deployment": {"fixed_replicas": true, "autoscaling": true},
      "topology": {"multi_node": true, "tensor_parallel": true, "pipeline_parallel": true},
      "routing": {"strategies": ["default", "prefix_affinity"], "kv_aware": "experimental"},
      "artifacts": {"local_path": true, "llmport_sync": true},
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Top-level capability sections that a driver may declare.  These are the
#: keys the spec sub-models correspond to (see ``schemas.py``).
CAPABILITY_SECTIONS: tuple[str, ...] = (
    "deployment",
    "service",
    "artifacts",
    "topology",
    "objective",
    "replication",
    "routing",
)

#: Minimal sections every driver implementation must declare.
DEFAULT_CAPABILITY_SECTIONS: tuple[str, ...] = (
    "deployment",
    "topology",
    "routing",
    "artifacts",
)


@dataclass
class CapabilityDocument:
    """A capability document for an environment.

    This is a thin, dependency-free representation of the JSON document a
    driver returns from ``InferenceDriver.capabilities``.  ``driver`` and
    ``backend_version`` are free-form so that no database enum migration is
    required for future backends.
    """

    driver: str
    backend_version: str | None = None
    sections: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityDocument:
        """Build a capability document from raw driver JSON."""
        if not isinstance(data, dict):
            msg = "capability document must be a JSON object"
            raise TypeError(msg)
        driver = data.get("driver", "unknown")
        backend_version = data.get("backend_version")
        if not isinstance(driver, str) or not driver:
            msg = "capability document requires a non-empty 'driver' string"
            raise ValueError(msg)
        return cls(
            driver=driver,
            backend_version=backend_version if isinstance(backend_version, str) else None,
            sections={
                k: v
                for k, v in data.items()
                if k in CAPABILITY_SECTIONS and isinstance(v, dict)
            },
            raw=data,
        )

    def to_json(self) -> str:
        """Serialise the raw capability document to JSON."""
        return json.dumps(self.raw if self.raw else {"driver": self.driver}, default=str)

    def section(self, name: str) -> dict[str, Any] | None:
        """
        Return a declared capability section.

        :param name: section key, e.g. ``"deployment"``.
        :return: the section's dict, or ``None`` if not declared.
        """
        return self.sections.get(name)

    def supports(self, name: str) -> bool:
        """
        Determine whether the environment declares a capability section.

        :param name: section key, e.g. ``"deployment"``.
        :return: True when the section is present in the document.
        """
        return name in self.sections

    def missing_required(self) -> list[str]:
        """
        List the missing required capability sections.

        :return: names from ``DEFAULT_CAPABILITY_SECTIONS`` that are absent.
        """
        return [name for name in DEFAULT_CAPABILITY_SECTIONS if name not in self.sections]
