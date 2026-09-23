"""Unit tests for host network and fabric discovery collectors."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import psutil
import socket

import pytest

from llm_port_node_agent.network import (
    NetworkInterfaceInfo,
    _detect_rdma_device_for_net_if,
    _parse_proc_net_route,
    _read_sysfs_file,
    collect_network_interfaces,
    summarize_network_inventory,
)


def test_collect_network_interfaces_smoke() -> None:
    """Smoke test: collecting network interfaces on the local host returns valid objects."""
    interfaces = collect_network_interfaces()
    assert isinstance(interfaces, list)
    # Most platforms have at least loopback or one adapter
    if interfaces:
        first = interfaces[0]
        assert isinstance(first, NetworkInterfaceInfo)
        assert isinstance(first.name, str)
        assert isinstance(first.mtu, int)
        summary = summarize_network_inventory(interfaces)
        assert isinstance(summary, dict)
        assert "fabrics" in summary
        assert "all_interfaces" in summary


def test_parse_proc_net_route(tmp_path: Path) -> None:
    """Test parsing /proc/net/route for default gateway."""
    route_file = tmp_path / "route"
    # Destination 00000000 with gateway 010AA80A (10.168.10.1 in little-endian hex)
    # 0A = 10, A8 = 168, 0A = 10, 01 = 1 -> 10.88.10.1 would be 010A580A
    route_file.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "enP7s7\t00000000\t010A580A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "enp1s0f1np1\t0000640A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n",
        encoding="utf-8",
    )
    iface, gw = _parse_proc_net_route(route_file)
    assert iface == "enP7s7"
    assert gw == "10.88.10.1"


def test_mock_dgx_spark_sysfs_roce(tmp_path: Path) -> None:
    """Simulate NVIDIA DGX Spark sysfs with 200 Gb/s RoCE and management network."""
    sys_net = tmp_path / "sys_class_net"
    sys_ib = tmp_path / "sys_class_infiniband"
    proc_route = tmp_path / "proc_net_route"

    # Setup proc route: enP7s7 is management default gateway
    proc_route.write_text(
        "Iface\tDestination\tGateway\n"
        "enP7s7\t00000000\t010A580A\n",
        encoding="utf-8",
    )

    # Interface 1: enp1s0f1np1 (200 Gb/s RoCE)
    roce_dir = sys_net / "enp1s0f1np1"
    roce_dir.mkdir(parents=True)
    (roce_dir / "operstate").write_text("up\n")
    (roce_dir / "mtu").write_text("9000\n")
    (roce_dir / "speed").write_text("200000\n")
    (roce_dir / "address").write_text("04:3f:72:bb:aa:11\n")

    # Mock PCI device path and symlink
    pci_name = "0000_01_00.1" if os.name == "nt" else "0000:01:00.1"
    pci_dev_target = tmp_path / "pci_devices" / pci_name
    pci_dev_target.mkdir(parents=True)
    device_link = roce_dir / "device"
    if os.name == "nt":
        # On Windows, create a directory instead of symlink
        device_link.mkdir(parents=True)
    else:
        device_link.symlink_to(pci_dev_target)

    # RDMA RoCE device under device/infiniband
    ib_dev_dir = device_link / "infiniband" / "rocep1s0f1"
    ports_dir = ib_dev_dir / "ports" / "1"
    ports_dir.mkdir(parents=True)
    (ports_dir / "link_layer").write_text("Ethernet\n")

    # Interface 2: enP7s7 (1 Gb/s Management)
    mgmt_dir = sys_net / "enP7s7"
    mgmt_dir.mkdir(parents=True)
    (mgmt_dir / "operstate").write_text("up\n")
    (mgmt_dir / "mtu").write_text("1500\n")
    (mgmt_dir / "speed").write_text("1000\n")
    (mgmt_dir / "address").write_text("b8:ce:f6:11:22:33\n")

    from collections import namedtuple

    MockSnicAddr = namedtuple("MockSnicAddr", ["family", "address", "netmask", "broadcast", "ptp"])

    # Mock psutil addresses
    mock_addrs = {
        "enp1s0f1np1": [
            MockSnicAddr(
                family=pytest.importorskip("socket").AF_INET,
                address="10.100.0.1",
                netmask="255.255.255.0",
                broadcast=None,
                ptp=None,
            )
        ],
        "enP7s7": [
            MockSnicAddr(
                family=pytest.importorskip("socket").AF_INET,
                address="10.88.10.49",
                netmask="255.255.255.0",
                broadcast=None,
                ptp=None,
            )
        ],
    }

    with patch("psutil.net_if_addrs", return_value=mock_addrs):
        interfaces = collect_network_interfaces(
            sys_net_path=sys_net,
            sys_ib_path=sys_ib,
            proc_route_path=proc_route,
        )

    assert len(interfaces) == 2
    by_name = {i.name: i for i in interfaces}

    roce_if = by_name["enp1s0f1np1"]
    assert roce_if.speed_mbps == 200000
    assert roce_if.speed_gbps == 200.0
    assert roce_if.mtu == 9000
    assert roce_if.link_type == "roce"
    assert roce_if.rdma_device == "rocep1s0f1"
    # Facts only: the agent reports the routing fact, never the "management"
    # verdict the backend planner is responsible for.
    assert roce_if.has_default_route is False
    assert not hasattr(roce_if, "is_management")
    assert len(roce_if.ips) == 1
    assert roce_if.ips[0].ip == "10.100.0.1"
    assert roce_if.ips[0].cidr == "10.100.0.0/24"

    mgmt_if = by_name["enP7s7"]
    assert mgmt_if.speed_mbps == 1000
    assert mgmt_if.speed_gbps == 1.0
    assert mgmt_if.mtu == 1500
    assert mgmt_if.link_type == "ethernet"
    assert mgmt_if.rdma_device is None
    assert mgmt_if.has_default_route is True
    assert mgmt_if.ips[0].ip == "10.88.10.49"

    # Verify normalized summary
    summary = summarize_network_inventory(interfaces)
    assert summary["default_route_interface"] == "enP7s7"
    assert summary["default_route_ip"] == "10.88.10.49"
    assert len(summary["fabrics"]) == 2
    fabrics_by_if = {f["interface"]: f for f in summary["fabrics"]}
    assert fabrics_by_if["enp1s0f1np1"]["link_type"] == "roce"
    assert fabrics_by_if["enp1s0f1np1"]["speed_gbps"] == 200.0
    # The planner needs these to reject unusable links and to draw the
    # management conclusion itself.
    assert fabrics_by_if["enp1s0f1np1"]["has_default_route"] is False
    assert fabrics_by_if["enP7s7"]["has_default_route"] is True
    assert all("is_management" not in f for f in summary["fabrics"])
    assert all(f["is_up"] is True for f in summary["fabrics"])


def test_docker_bridges_and_veths_are_typed_virtual(tmp_path: Path) -> None:
    """Container plumbing must be reported as such, and veths kept out of Tier 2.

    On the real DGX head ``docker0`` (172.17.0.1/16) and five ``br-*`` bridges
    carry *identical* addresses to the ones on the worker, so a planner that
    only asks "is this subnet on every node?" happily binds the whole cluster
    to 172.17.0.1.  ``veth*`` devices additionally churn on every container
    start; there are ~20 of them and a snapshot row is written per tick.
    """
    sys_net = tmp_path / "sys_class_net"
    sys_ib = tmp_path / "sys_class_infiniband"
    proc_route = tmp_path / "proc_net_route"
    proc_route.write_text("Iface\tDestination\tGateway\n", encoding="utf-8")

    for name in ("docker0", "br-7c067fdc1b1d", "veth1a2b3c", "enp1s0f1np1"):
        d = sys_net / name
        d.mkdir(parents=True)
        (d / "operstate").write_text("up\n")
        (d / "mtu").write_text("1500\n")
    (sys_net / "docker0" / "bridge").mkdir()

    from collections import namedtuple
    import socket

    MockSnicAddr = namedtuple("MockSnicAddr", ["family", "address", "netmask", "broadcast", "ptp"])
    mock_addrs = {
        "docker0": [MockSnicAddr(socket.AF_INET, "172.17.0.1", "255.255.0.0", None, None)],
        "br-7c067fdc1b1d": [MockSnicAddr(socket.AF_INET, "172.18.0.1", "255.255.0.0", None, None)],
        "veth1a2b3c": [],
        "enp1s0f1np1": [MockSnicAddr(socket.AF_INET, "10.100.0.1", "255.255.255.0", None, None)],
    }

    with patch("psutil.net_if_addrs", return_value=mock_addrs):
        interfaces = collect_network_interfaces(
            sys_net_path=sys_net, sys_ib_path=sys_ib, proc_route_path=proc_route,
        )

    by_name = {i.name: i for i in interfaces}
    assert by_name["docker0"].link_type == "virtual"
    assert by_name["br-7c067fdc1b1d"].link_type == "virtual"
    assert by_name["veth1a2b3c"].link_type == "virtual"
    assert by_name["enp1s0f1np1"].link_type == "ethernet"
    assert by_name["enp1s0f1np1"].is_virtual is False

    summary = summarize_network_inventory(interfaces)
    fabrics = {f["interface"]: f for f in summary["fabrics"]}
    assert fabrics["docker0"]["link_type"] == "virtual"
    assert fabrics["enp1s0f1np1"]["is_virtual"] is False
    # Tier-2 detail drops the ephemeral veth pair.
    assert "veth1a2b3c" not in {i["name"] for i in summary["all_interfaces"]}
    assert summary["omitted_ephemeral_interfaces"] == 1


def test_vlan_and_bond_links_are_not_treated_as_virtual(tmp_path: Path) -> None:
    """A VLAN or bond on real NICs is a legitimate fabric, not plumbing.

    Neither carries a sysfs ``device`` symlink, so "no device link means
    virtual" would silently disqualify a perfectly good interconnect.
    """
    sys_net = tmp_path / "sys_class_net"
    sys_ib = tmp_path / "sys_class_infiniband"
    proc_route = tmp_path / "proc_net_route"
    proc_route.write_text("Iface\tDestination\tGateway\n", encoding="utf-8")

    for name in ("bond0", "enp1s0f1np1.100"):
        d = sys_net / name
        d.mkdir(parents=True)
        (d / "operstate").write_text("up\n")
        (d / "mtu").write_text("9000\n")

    from collections import namedtuple
    import socket

    MockSnicAddr = namedtuple("MockSnicAddr", ["family", "address", "netmask", "broadcast", "ptp"])
    mock_addrs = {
        "bond0": [MockSnicAddr(socket.AF_INET, "10.100.2.1", "255.255.255.0", None, None)],
        "enp1s0f1np1.100": [
            MockSnicAddr(socket.AF_INET, "10.100.3.1", "255.255.255.0", None, None)
        ],
    }
    with patch("psutil.net_if_addrs", return_value=mock_addrs):
        interfaces = collect_network_interfaces(
            sys_net_path=sys_net, sys_ib_path=sys_ib, proc_route_path=proc_route,
        )
    assert all(i.link_type == "ethernet" for i in interfaces)
    assert all(i.is_virtual is False for i in interfaces)


def test_default_route_is_never_guessed(tmp_path: Path) -> None:
    """An unreadable routing table yields no default-route claim at all.

    The previous fallback labelled the first UP ethernet with an IPv4 (in
    sorted-name order) as the default route, which drives the planner's
    -5000 management penalty onto an arbitrary link.
    """
    sys_net = tmp_path / "sys_class_net"
    sys_ib = tmp_path / "sys_class_infiniband"
    missing_route = tmp_path / "no_such_route"

    for name in ("aaa0", "zzz0"):
        d = sys_net / name
        d.mkdir(parents=True)
        (d / "operstate").write_text("up\n")
        (d / "mtu").write_text("1500\n")

    from collections import namedtuple
    import socket

    MockSnicAddr = namedtuple("MockSnicAddr", ["family", "address", "netmask", "broadcast", "ptp"])
    mock_addrs = {
        "aaa0": [MockSnicAddr(socket.AF_INET, "10.1.0.1", "255.255.255.0", None, None)],
        "zzz0": [MockSnicAddr(socket.AF_INET, "10.2.0.1", "255.255.255.0", None, None)],
    }
    with patch("psutil.net_if_addrs", return_value=mock_addrs):
        interfaces = collect_network_interfaces(
            sys_net_path=sys_net, sys_ib_path=sys_ib, proc_route_path=missing_route,
        )
    assert all(i.has_default_route is False for i in interfaces)
    summary = summarize_network_inventory(interfaces)
    assert summary["default_route_interface"] is None


def test_infiniband_link_type_detection(tmp_path: Path) -> None:
    """Test InfiniBand link_layer detection (e.g. mlx5_0 with InfiniBand)."""
    sys_net = tmp_path / "net"
    ib_if = sys_net / "ib0"
    ib_dev_dir = ib_if / "device" / "infiniband" / "mlx5_0"
    ports_dir = ib_dev_dir / "ports" / "1"
    ports_dir.mkdir(parents=True)
    (ports_dir / "link_layer").write_text("InfiniBand\n")

    rdma_dev, link_type = _detect_rdma_device_for_net_if(ib_if, tmp_path / "nonexistent")
    assert rdma_dev == "mlx5_0"
    assert link_type == "infiniband"


def test_speed_error_handling(tmp_path: Path) -> None:
    """Test that speed errors (e.g. -1 or unreadable) are handled gracefully."""
    sys_net = tmp_path / "net"
    veth = sys_net / "veth0"
    veth.mkdir(parents=True)
    (veth / "speed").write_text("-1\n")  # Virtual or unnegotiated links report -1
    (veth / "mtu").write_text("1500\n")
    (veth / "operstate").write_text("down\n")

    interfaces = collect_network_interfaces(
        sys_net_path=sys_net,
        sys_ib_path=tmp_path / "ib",
        proc_route_path=tmp_path / "route",
    )
    by_name = {i.name: i for i in interfaces}
    assert by_name["veth0"].speed_mbps is None
    assert by_name["veth0"].speed_gbps is None
    assert by_name["veth0"].operstate == "down"


def _free_port() -> int:
    """A port the OS has just confirmed is bindable.

    These tests used to hardcode 45474-45479. On a machine running WSL2 with
    mirrored networking, Hyper-V reserves wide blocks of the ephemeral range
    -- 45470-45489 entirely, here -- and a reserved port is invisible to
    netstat, so six tests failed with "only one usage of each socket address"
    against a port nothing appeared to hold. Asking the OS for a free one
    removes the guess.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.asyncio
async def test_ephemeral_probe_handshake_success() -> None:
    """Test ephemeral active probe listener and connect handshake."""
    import asyncio
    from llm_port_node_agent.network import run_ephemeral_connect, run_ephemeral_listener

    # Pick a dynamic high port
    port = _free_port()
    probe_token = "secret-token-123"

    listener_task = asyncio.create_task(
        run_ephemeral_listener(ip="127.0.0.1", port=port, timeout_sec=2.0, probe_token=probe_token)
    )
    # Give listener tiny moment to bind
    await asyncio.sleep(0.05)

    connect_result = await run_ephemeral_connect(
        target_ip="127.0.0.1", target_port=port, timeout_sec=2.0, probe_token=probe_token
    )
    listener_result = await listener_task

    assert connect_result["reachable"] is True
    assert isinstance(connect_result["rtt_ms"], float)
    assert connect_result["rtt_ms"] >= 0.0
    assert listener_result["listening"] is True
    assert listener_result["connected"] is True


@pytest.mark.asyncio
async def test_ephemeral_probe_token_mismatch() -> None:
    """Test that mismatched probe token is rejected."""
    import asyncio
    from llm_port_node_agent.network import run_ephemeral_connect, run_ephemeral_listener

    port = _free_port()
    listener_task = asyncio.create_task(
        run_ephemeral_listener(ip="127.0.0.1", port=port, timeout_sec=2.0, probe_token="expected-token")
    )
    await asyncio.sleep(0.05)

    connect_result = await run_ephemeral_connect(
        target_ip="127.0.0.1", target_port=port, timeout_sec=2.0, probe_token="wrong-token"
    )
    listener_result = await listener_task

    assert connect_result["reachable"] is False
    assert "probe rejected" in connect_result["error"]



@pytest.mark.asyncio
async def test_validate_fabric_commands_complete_the_challenge() -> None:
    """The probes must be reachable as node commands, not just as functions.

    Until they were wired to command types, ``run_ephemeral_listener`` /
    ``run_ephemeral_connect`` were reachable only from this test file - both
    ends driven against 127.0.0.1 in one process - so every recommendation
    rested on passive sysfs facts with no proof the nodes could reach each
    other on the chosen CIDR.
    """
    import asyncio

    from llm_port_node_agent.dispatcher import CommandDispatcher
    from llm_port_node_agent.models import NodeCommandType

    dispatcher = CommandDispatcher.__new__(CommandDispatcher)
    port = _free_port()
    token = "challenge-token"

    async def _noop_progress(_payload: dict) -> None:
        return None

    listen = asyncio.create_task(
        dispatcher._execute(
            command_type=NodeCommandType.VALIDATE_FABRIC_LISTEN.value,
            payload={"ip": "127.0.0.1", "port": port, "probe_token": token, "timeout_sec": 2.0},
            emit_progress=_noop_progress,
        )
    )
    await asyncio.sleep(0.05)

    connect_result = await dispatcher._execute(
        command_type=NodeCommandType.VALIDATE_FABRIC_CONNECT.value,
        payload={
            "target_ip": "127.0.0.1",
            "target_port": port,
            "source_ip": "127.0.0.1",
            "probe_token": token,
            "timeout_sec": 2.0,
        },
        emit_progress=_noop_progress,
    )
    listen_result = await listen

    assert listen_result["listening"] is True
    assert listen_result["connected"] is True
    assert connect_result["reachable"] is True
    assert connect_result["target_ip"] == "127.0.0.1"
    assert connect_result["target_port"] == port


@pytest.mark.asyncio
async def test_validate_fabric_connect_reports_an_unreachable_peer() -> None:
    """A failed challenge is a structured result, not an exception."""
    from llm_port_node_agent.network import handle_validate_fabric_connect

    result = await handle_validate_fabric_connect(
        {"target_ip": "127.0.0.1", "target_port": 45461, "timeout_sec": 0.5}
    )
    assert result["reachable"] is False
    assert result["error"]


@pytest.mark.asyncio
async def test_validate_fabric_listen_rejects_a_bad_payload() -> None:
    """A malformed probe request fails cleanly instead of binding something odd."""
    from llm_port_node_agent.network import handle_validate_fabric_listen

    assert (await handle_validate_fabric_listen({"port": 45470}))["listening"] is False
    bad_port = await handle_validate_fabric_listen({"ip": "127.0.0.1", "port": 0})
    assert bad_port["listening"] is False


@pytest.mark.asyncio
async def test_listener_serves_one_probe_per_peer() -> None:
    """A 3-node environment sends two probes at one listener.

    A listener that closed after the first connection would fail every probe
    but one, and the plan would report the fabric as unreachable.
    """
    import asyncio

    from llm_port_node_agent.network import run_ephemeral_connect, run_ephemeral_listener

    port = _free_port()
    token = "multi-peer-token"
    listener = asyncio.create_task(
        run_ephemeral_listener(
            ip="127.0.0.1", port=port, timeout_sec=5.0, probe_token=token, expected_probes=2,
        )
    )
    await asyncio.sleep(0.05)

    first = await run_ephemeral_connect("127.0.0.1", port, timeout_sec=2.0, probe_token=token)
    second = await run_ephemeral_connect("127.0.0.1", port, timeout_sec=2.0, probe_token=token)
    result = await listener

    assert first["reachable"] is True
    assert second["reachable"] is True
    assert result["connected"] is True
    assert result["probes_accepted"] == 2


@pytest.mark.asyncio
async def test_prober_waits_out_a_listener_that_starts_late() -> None:
    """Listener and probes are separate commands; their order is not guaranteed.

    Without the retry window the challenge would fail - and the plan would
    declare a perfectly good fabric a blocker - whenever the prober's agent
    happened to pick up its command first.
    """
    import asyncio

    from llm_port_node_agent.network import (
        handle_validate_fabric_connect,
        run_ephemeral_listener,
    )

    port = _free_port()
    token = "late-listener"

    async def _late_listener() -> dict:
        # Longer than a single dial window, so the retry loop is what closes
        # the gap rather than one generous connect timeout.
        await asyncio.sleep(1.2)
        return await run_ephemeral_listener(
            ip="127.0.0.1", port=port, timeout_sec=5.0, probe_token=token,
        )

    listener = asyncio.create_task(_late_listener())
    connect_result = await handle_validate_fabric_connect({
        "target_ip": "127.0.0.1",
        "target_port": port,
        "probe_token": token,
        "timeout_sec": 0.4,
        "retry_for_sec": 8.0,
    })
    listen_result = await listener

    assert connect_result["reachable"] is True
    assert connect_result["attempts"] > 1
    assert listen_result["connected"] is True


@pytest.mark.asyncio
async def test_prober_does_not_retry_a_definitive_rejection() -> None:
    """A rejected token is an answer, not a race; it must not burn the window."""
    import asyncio

    from llm_port_node_agent.network import (
        handle_validate_fabric_connect,
        run_ephemeral_listener,
    )

    port = _free_port()
    listener = asyncio.create_task(
        run_ephemeral_listener(
            ip="127.0.0.1", port=port, timeout_sec=3.0, probe_token="expected-token",
        )
    )
    await asyncio.sleep(0.05)

    result = await handle_validate_fabric_connect({
        "target_ip": "127.0.0.1",
        "target_port": port,
        "probe_token": "wrong-token",
        "timeout_sec": 2.0,
        "retry_for_sec": 10.0,
    })
    listener.cancel()

    assert result["reachable"] is False
    assert result["attempts"] == 1
    assert "probe rejected" in result["error"]
