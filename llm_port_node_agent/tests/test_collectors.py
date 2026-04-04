"""Tests for collectors — inventory and utilization with mocked system calls."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from llm_port_node_agent.collectors import (
    _disk_root,
    collect_gpu_snapshot,
    collect_inventory,
    collect_utilization,
)
from llm_port_node_agent.gpu import GpuCollector, GpuSnapshot, GpuDevice, NullCollector


class FakeGpuCollector:
    """Deterministic GPU collector for testing."""

    def __init__(self, snapshot: GpuSnapshot | None = None) -> None:
        self._snapshot = snapshot or GpuSnapshot()

    async def snapshot(self) -> GpuSnapshot:
        return self._snapshot

    async def device_count(self) -> int:
        return self._snapshot.count

    @property
    def vendor(self) -> str:
        return "fake"


def _fake_vmem() -> MagicMock:
    vm = MagicMock()
    vm.total = 16 * 1024**3
    vm.used = 8 * 1024**3
    vm.available = 8 * 1024**3
    vm.percent = 50.0
    return vm


def _fake_disk() -> MagicMock:
    du = MagicMock()
    du.total = 500 * 1024**3
    du.used = 200 * 1024**3
    du.free = 300 * 1024**3
    du.percent = 40.0
    return du


def _fake_net() -> MagicMock:
    n = MagicMock()
    n.bytes_sent = 1000
    n.bytes_recv = 2000
    n.packets_sent = 10
    n.packets_recv = 20
    return n


@pytest.mark.asyncio()
async def test_gpu_snapshot_no_gpu() -> None:
    collector = NullCollector()
    snap = await collect_gpu_snapshot(collector)
    assert snap["count"] == 0
    assert snap["free_vram_bytes"] == 0


@pytest.mark.asyncio()
async def test_gpu_snapshot_with_collector() -> None:
    collector = FakeGpuCollector(
        GpuSnapshot(
            count=1,
            devices=[GpuDevice(memory_total_mib=8192, memory_used_mib=2048, utilization_pct=45, temperature_c=65, vendor="nvidia")],
            total_vram_bytes=8192 * 1024 * 1024,
            used_vram_bytes=2048 * 1024 * 1024,
            free_vram_bytes=(8192 - 2048) * 1024 * 1024,
        ),
    )
    snap = await collect_gpu_snapshot(collector)
    assert snap["count"] == 1
    assert snap["devices"][0]["memory_total_mib"] == 8192
    assert snap["free_vram_bytes"] == (8192 - 2048) * 1024 * 1024


@pytest.mark.asyncio()
@patch("llm_port_node_agent.collectors.psutil")
async def test_collect_inventory_with_gpu_snapshot(mock_psutil: MagicMock) -> None:
    mock_psutil.virtual_memory.return_value = _fake_vmem()
    mock_psutil.disk_usage.return_value = _fake_disk()
    mock_psutil.cpu_count.return_value = 8
    mock_psutil.net_if_addrs.return_value = {"eth0": []}

    gpu = {"count": 2, "free_vram_bytes": 4096}
    inv = await collect_inventory({"max_parallel": 2}, gpu_snapshot=gpu)
    assert inv["gpu_count"] == 2
    assert inv["cpu_count_logical"] == 8
    # Should not call collect_gpu_snapshot since we passed a snapshot
    assert inv["gpu"] is gpu


@pytest.mark.asyncio()
@patch("llm_port_node_agent.collectors.psutil")
async def test_collect_utilization_with_gpu_snapshot(mock_psutil: MagicMock) -> None:
    mock_psutil.cpu_percent.return_value = 25.0
    mock_psutil.virtual_memory.return_value = _fake_vmem()
    mock_psutil.disk_usage.return_value = _fake_disk()
    mock_psutil.net_io_counters.return_value = _fake_net()

    gpu = {"count": 1, "free_vram_bytes": 2048}
    util = await collect_utilization(gpu_snapshot=gpu)
    assert util["cpu_percent"] == 25.0
    assert util["gpu_free_vram_bytes"] == 2048
    assert util["gpu"] is gpu


@pytest.mark.asyncio()
@patch("llm_port_node_agent.collectors.psutil")
async def test_collect_utilization_network_fields(mock_psutil: MagicMock) -> None:
    mock_psutil.cpu_percent.return_value = 10.0
    mock_psutil.virtual_memory.return_value = _fake_vmem()
    mock_psutil.disk_usage.return_value = _fake_disk()
    mock_psutil.net_io_counters.return_value = _fake_net()

    util = await collect_utilization(gpu_snapshot={"count": 0, "free_vram_bytes": 0})
    assert util["network"]["bytes_sent"] == 1000
    assert util["network"]["packets_recv"] == 20


@patch("llm_port_node_agent.collectors.sys")
def test_disk_root_unix(mock_sys: MagicMock) -> None:
    mock_sys.platform = "linux"
    assert _disk_root() == "/"


@patch("llm_port_node_agent.collectors.sys")
@patch.dict("os.environ", {"SystemDrive": "D:"})
def test_disk_root_windows(mock_sys: MagicMock) -> None:
    mock_sys.platform = "win32"
    import os
    assert _disk_root() == "D:" + os.sep
