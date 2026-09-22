"""The vendor has to survive the trip from the backend's launch payload.

The mapping in ``runtimes/accelerators.py`` is only useful if the spec
actually carries a vendor: if ``from_payload`` dropped it, every node would
silently fall back to NVIDIA flags and the genericity would be cosmetic.
"""

from __future__ import annotations

from llm_port_node_agent.ray.container import RuntimeBundleSpec

_BASE = {
    "image": "llm-port/ray-runtime:2.58.0-cu130",
    "digest": "sha256:" + "0" * 64,
    "requirements": {"gpus": "all"},
}


def test_vendor_comes_from_target_architecture() -> None:
    spec = RuntimeBundleSpec.from_payload(
        {**_BASE, "target_architecture": {"cpu": "x86_64", "accelerator_vendor": "amd"}}
    )
    assert spec.accelerator_vendor == "amd"


def test_requirements_may_carry_it_instead() -> None:
    # Older payloads put it next to the other container requirements.
    spec = RuntimeBundleSpec.from_payload(
        {**_BASE, "requirements": {"gpus": "all", "accelerator_vendor": "intel"}}
    )
    assert spec.accelerator_vendor == "intel"


def test_absent_vendor_defaults_to_nvidia() -> None:
    # Every bundle shipped so far, including the certified DGX one.
    assert RuntimeBundleSpec.from_payload(_BASE).accelerator_vendor == "nvidia"
