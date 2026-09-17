"""Typed errors for the node agent's Ray integration (spec section 27).

Every failure mode that a backend operator can act on maps to exactly one
exception type.  The dispatcher converts these into the standard
``success=False`` / ``error`` envelope; the reconciler maps the *error codes*
(reports) to ``EnvironmentCondition`` reasons.

Hierarchy::

    RayError
    ├── RayAttachError            cannot reach/attach to the local cluster
    ├── RayVersionMismatchError   attached cluster version != packaged SDK version
    ├── RayRuntimeError           CLI bootstrap failed (ray start/stop exit codes)
    ├── RayServeError             Serve Python API failure
    └── RayMetricsDiscoveryError  metrics target discovery failed (Tier C, optional)

All errors carry a stable machine-readable ``code`` so the command result
envelope can carry it verbatim without string matching.
"""

from __future__ import annotations


class RayError(Exception):
    """Base class for all typed Ray-agent errors."""

    #: Stable code surfaced in command-result envelopes (Section 27).
    code: str = "ray_error"
    #: Whether the condition is expected/recoverable without operator action.
    recoverable: bool = False

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.detail = detail

    @property
    def report(self) -> dict[str, str]:
        """Serializable error report: code + message + optional detail."""
        report = {"code": self.code, "message": str(self)}
        if self.detail:
            report["detail"] = self.detail
        return report


class RayAttachError(RayError):
    """The agent could not attach to a local Ray cluster via the SDK.

    Raised when ``ray.init(address="auto")`` fails or a post-attach GCS
    round-trip (``ray.nodes()``) fails.  ``GET_RAY_STATUS`` maps this to
    ``alive=False`` (the cluster is simply not up on this node); it is NOT a
    command failure by itself.
    """

    code = "ray_attach_failed"
    recoverable = True


class RayVersionMismatchError(RayAttachError):
    """The attached cluster runs a different Ray version than the SDK.

    The agent packages ``ray[serve]==2.58.0``; the bootstrap CLI comes from the
    same wheel, so parity is expected.  A mismatch means the node is running a
    stray/older Ray installation — an operator action item.
    """

    code = "ray_version_mismatch"


class RayRuntimeError(RayError):
    """A bootstrap/process CLI operation failed (``ray start`` / ``ray stop``)."""

    code = "ray_runtime_error"


class RayServeError(RayError):
    """A Ray Serve Python API operation failed."""

    code = "ray_serve_error"


class RayMetricsDiscoveryError(RayError):
    """Metrics discovery failed (optional Tier C capability).

    Never propagates into cluster health: metrics/Prometheus failure must not
    fail ``GET_RAY_STATUS``.
    """

    code = "ray_metrics_discovery_failed"
    recoverable = True
