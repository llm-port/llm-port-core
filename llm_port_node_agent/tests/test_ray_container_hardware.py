"""Image identity against the real DGX Spark images (Phase 4B, F-05).

``tests/fixtures/dgx_spark_image_identity.json`` is the verbatim output of
``docker image inspect llmport/ray-vllm-gb10:ray2.58-nv26.08`` on both nodes
on 2026-09-20.  It records the situation digest pinning exists to catch, and
the one it must not mistake for it:

    spark-ts3202 (head)   .Id sha256:7dc13b9a...   50 layers
    spark-3201   (worker) .Id sha256:d36c047d...   the *same* 50 layers

The nodes hold byte-identical content under different config IDs, because the
copy was side-loaded rather than pulled.  ``runtime-manifest.json`` meanwhile
records a third id (``sha256:d5dd2c6a...``) that exists on neither node.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from llm_port_node_agent.ray.container import (
    RayContainerRuntime,
    RuntimeBundleSpec,
    RuntimeDigestMismatch,
    compute_rootfs_digest,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "dgx_spark_image_identity.json"

# The values the backend bundle pins, from the same inspection.
BUNDLE_DIGEST = "sha256:7dc13b9aff5a00dc447251d550a29bcddf9480cc7efad1509cfbd9a0c661a9d8"
BUNDLE_ROOTFS = "sha256:e5e139aba1deaaccff763993a4f4ca5a477d8d157cef5c72f8081b2b863d58d8"
# The identity runtime-manifest.json claims, which is on neither node.
STALE_MANIFEST_DIGEST = (
    "sha256:d5dd2c6ad48e571db57b59f80e8faf62814f6a8db0b8bb86067c8647a95ce7f3"
)


def _identities() -> dict[str, dict[str, Any]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class _RecordedRuntime:
    """Replays one node's real ``docker image inspect`` result."""

    def __init__(self, identity: dict[str, Any]) -> None:
        self._identity = identity

    @property
    def name(self) -> str:
        return "docker"

    async def image_identity(self, image: str, *, timeout_sec: float = 20) -> dict[str, Any]:
        return dict(self._identity)


def _spec(**overrides: Any) -> RuntimeBundleSpec:
    payload: dict[str, Any] = {
        "image": "llmport/ray-vllm-gb10:ray2.58-nv26.08",
        "digest": BUNDLE_DIGEST,
        "rootfs_digest": BUNDLE_ROOTFS,
        "requirements": {},
        "mounts": [],
        "env": {},
    }
    payload.update(overrides)
    return RuntimeBundleSpec.from_payload(payload)


def test_the_two_nodes_really_do_disagree_on_config_id() -> None:
    """Guard the premise: this is a recorded fact, not a constructed scenario."""
    ids = _identities()
    head, worker = ids["spark-ts3202"], ids["spark-3201"]

    assert head["id"] != worker["id"], "the drift this fixture exists for is gone"
    assert head["rootfs_layers"] == worker["rootfs_layers"]
    assert len(head["rootfs_layers"]) == 50
    assert compute_rootfs_digest(head["rootfs_layers"]) == BUNDLE_ROOTFS
    # The worker was side-loaded, so it has no registry manifest digest at all
    # - which is exactly why repo_digest cannot be the primary identity.
    assert worker["repo_digests"] == []
    # And the certification manifest points at an image neither node has.
    assert STALE_MANIFEST_DIGEST not in {head["id"], worker["id"]}


@pytest.mark.anyio()
@pytest.mark.parametrize("hostname", ["spark-ts3202", "spark-3201"])
async def test_both_real_nodes_verify_against_the_bundle(hostname: str) -> None:
    """Verification must pass on both nodes, not just the one that built it.

    Checking ``.Id`` alone passed on the head and failed on the worker, which
    would have blocked every deployment there while the worker was running
    exactly the certified bits.
    """
    container = RayContainerRuntime(runtime=_RecordedRuntime(_identities()[hostname]))

    result = await container.ensure_image(_spec())

    assert result["verified"] is True
    assert result["rootfs_digest"] == BUNDLE_ROOTFS


@pytest.mark.anyio()
async def test_the_worker_would_fail_a_config_id_only_pin() -> None:
    """The precise failure the content identity fixes."""
    container = RayContainerRuntime(runtime=_RecordedRuntime(_identities()["spark-3201"]))

    with pytest.raises(RuntimeDigestMismatch):
        await container.ensure_image(_spec(rootfs_digest=None))


@pytest.mark.anyio()
@pytest.mark.parametrize("hostname", ["spark-ts3202", "spark-3201"])
async def test_the_stale_manifest_identity_matches_neither_node(hostname: str) -> None:
    """A bundle generated from runtime-manifest.json rejects both nodes.

    This is the mechanism working, not a bug: the manifest describes a build
    that no longer exists anywhere, and a catalog that pins it is
    authoritative-looking and wrong.
    """
    container = RayContainerRuntime(runtime=_RecordedRuntime(_identities()[hostname]))

    with pytest.raises(RuntimeDigestMismatch) as excinfo:
        await container.ensure_image(
            _spec(digest=STALE_MANIFEST_DIGEST, rootfs_digest=None)
        )
    assert STALE_MANIFEST_DIGEST in str(excinfo.value)
