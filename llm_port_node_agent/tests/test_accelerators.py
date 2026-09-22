"""The semantic accelerator request must not assume one vendor's CLI.

``ContainerRequirements.gpus`` says *what* the runtime needs; the handler says
how to spell it.  Before this mapping existed the docker handler emitted
``--gpus`` for everything -- an NVIDIA container toolkit flag that a ROCm or
Level Zero host rejects outright -- so a non-NVIDIA node could never have
joined without editing the bundle format itself.

The NVIDIA cases below are the regression guard: certified hardware must keep
producing byte-identical flags.
"""

from __future__ import annotations

import pytest

from llm_port_node_agent.runtimes.accelerators import (
    accelerator_cdi_flags,
    accelerator_run_flags,
)


class TestNvidiaUnchanged:
    """The path every existing bundle takes."""

    def test_all(self) -> None:
        assert accelerator_run_flags("nvidia", "all") == ["--gpus", "all"]

    def test_count_passes_through(self) -> None:
        # ``docker run --gpus 2`` is valid; no enumeration needed.
        assert accelerator_run_flags("nvidia", "2") == ["--gpus", "2"]

    def test_missing_vendor_means_nvidia(self) -> None:
        # Bundles written before ``accelerator_vendor`` existed carry no vendor.
        assert accelerator_run_flags(None, "all") == ["--gpus", "all"]

    def test_cuda_is_an_alias(self) -> None:
        assert accelerator_run_flags("cuda", "all") == ["--gpus", "all"]


class TestOtherVendors:
    def test_amd_exposes_kfd_and_render_nodes(self) -> None:
        flags = accelerator_run_flags("amd", "all")
        assert "--device=/dev/kfd" in flags
        assert "--device=/dev/dri" in flags
        assert "--gpus" not in flags

    def test_rocm_is_an_alias_for_amd(self) -> None:
        assert accelerator_run_flags("rocm", "all") == accelerator_run_flags("amd", "all")

    def test_intel_exposes_render_nodes_only(self) -> None:
        # No kernel fusion driver on Intel; /dev/dri is the whole story.
        assert accelerator_run_flags("intel", "all") == ["--device=/dev/dri", "--group-add=video"]

    def test_apple_asks_for_nothing(self) -> None:
        # Metal is not reachable from a Linux container: a backend targeting
        # Apple silicon runs on the host, so there is no flag to emit.
        assert accelerator_run_flags("apple", "all") == []

    def test_unknown_vendor_asks_for_nothing(self) -> None:
        # Guessing a flag would fail at container start with a runtime error
        # that says nothing useful; emitting none fails in the workload, where
        # the missing device is visible.
        assert accelerator_run_flags("some-npu", "all") == []


@pytest.mark.parametrize("vendor", ["nvidia", "amd", "intel", "apple", None])
@pytest.mark.parametrize("request_", [None, "", "none", "None", "0"])
def test_nothing_requested_means_no_flags(vendor: str | None, request_: str | None) -> None:
    assert accelerator_run_flags(vendor, request_) == []
    assert accelerator_cdi_flags(vendor, request_) == []


class TestCdi:
    """Podman names the vendor inside the device, not in a flag."""

    def test_nvidia_unchanged(self) -> None:
        assert accelerator_cdi_flags("nvidia", "all") == ["--device", "nvidia.com/gpu=all"]

    def test_vendor_changes_the_device_name(self) -> None:
        assert accelerator_cdi_flags("amd", "all") == ["--device", "amd.com/gpu=all"]
        assert accelerator_cdi_flags("intel", "all") == ["--device", "intel.com/gpu=all"]

    def test_count_enumerates_devices(self) -> None:
        # CDI names one device at a time, so a count has to be expanded.
        assert accelerator_cdi_flags("nvidia", "2") == [
            "--device", "nvidia.com/gpu=0",
            "--device", "nvidia.com/gpu=1",
        ]

    def test_unmapped_vendor_asks_for_nothing(self) -> None:
        assert accelerator_cdi_flags("apple", "all") == []


# ── finding a vendor CLI the service manager's PATH does not have ────────


def test_find_tool_searches_beyond_path(tmp_path, monkeypatch) -> None:
    """WSL2 projects the driver somewhere systemd's PATH does not reach.

    `/usr/lib/wsl/lib` is on an interactive shell's PATH and not on a unit's,
    so the agent saw the GPU when run by hand and reported `gpu_vendor: none`
    when run as a service -- and a node with no accelerator resolves to no
    runtime bundle, so its cluster refused it.
    """
    from llm_port_node_agent.gpu import find_tool

    wsl_lib = tmp_path / "wsl" / "lib"
    wsl_lib.mkdir(parents=True)
    smi = wsl_lib / "nvidia-smi"
    smi.write_text("#!/bin/sh\n")
    smi.chmod(0o755)

    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "llm_port_node_agent.gpu._TOOL_SEARCH_PATH", (str(wsl_lib),)
    )
    assert find_tool("nvidia-smi") == str(smi)


def test_find_tool_prefers_path(monkeypatch) -> None:
    """A tool on PATH is the answer; the extra locations are a fallback."""
    from llm_port_node_agent.gpu import find_tool

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/nvidia-smi")
    assert find_tool("nvidia-smi") == "/usr/bin/nvidia-smi"


def test_find_tool_reports_nothing_when_absent(monkeypatch, tmp_path) -> None:
    """Absent is absent -- never a bare name the caller would shell out to."""
    from llm_port_node_agent.gpu import find_tool

    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "llm_port_node_agent.gpu._TOOL_SEARCH_PATH", (str(tmp_path),)
    )
    assert find_tool("nvidia-smi") is None
