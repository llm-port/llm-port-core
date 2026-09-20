"""Host network and fabric discovery collectors for node agents.

Discovers physical, virtual, and RDMA/RoCE interfaces on the host:
- Scans Linux ``/sys/class/net`` and ``/sys/class/infiniband`` when available.
- Correlates IP addresses, CIDRs, and netmasks via ``psutil.net_if_addrs``.
- Detects link speed, MTU, carrier operstate, and PCI device IDs.
- Correlates RDMA devices (e.g. ``rocep1s0f1`` or ``mlx5_0``) to identify RoCE/IB fabrics.
- Reports the default-route fact via ``/proc/net/route`` (never guessed).
- Classifies kernel-virtual devices (bridges, veth pairs, tunnels) as
  ``link_type="virtual"`` so the planner can exclude them from candidacy.
- Provides graceful fallback on non-Linux systems or virtualized test environments.

Boundary (03_PHASED_MIGRATION_PLAN.md 4A): the agent reports **facts only**.
``has_default_route`` is a fact read out of the routing table; conclusions
drawn from it (``is_management``, ``is_dedicated``, ``recommended``) belong to
the backend planner and are deliberately not computed here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

log = logging.getLogger(__name__)

DEFAULT_SYS_NET = Path("/sys/class/net")
DEFAULT_SYS_IB = Path("/sys/class/infiniband")
DEFAULT_PROC_ROUTE = Path("/proc/net/route")

# Kernel-virtual device name prefixes: container/VM plumbing that can never
# carry a cluster fabric.  Deliberately does NOT include VLAN or bond devices
# (``eth0.100``, ``bond0``) - those sit on real NICs and are legitimate
# interconnects, so "has no sysfs ``device`` symlink" is the wrong test.
_VIRTUAL_NAME_PREFIXES = (
    "docker", "br-", "veth", "virbr", "vnet", "vif",
    "tun", "tap", "cni", "flannel", "kube", "cali", "lxc", "lxd",
    "ovs-", "wg", "zt", "tailscale", "ham", "dummy", "ifb",
    "nomad", "podman", "cilium",
)

# Ephemeral per-container device prefixes.  These churn on every container
# start/stop, so they are dropped from the Tier-2 snapshot (a DGX head carries
# ~20 of them and a snapshot row is written on every inventory tick).
_EPHEMERAL_NAME_PREFIXES = ("veth", "vif", "vnet", "tap")


@dataclass
class IPAddressInfo:
    """IPv4 or IPv6 address assigned to an interface."""

    ip: str
    netmask: str | None = None
    cidr: str | None = None
    family: str = "ipv4"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NetworkInterfaceInfo:
    """Detailed hardware and network configuration for a single interface."""

    name: str
    ips: list[IPAddressInfo] = field(default_factory=list)
    mac: str | None = None
    speed_mbps: int | None = None
    speed_gbps: float | None = None
    operstate: str = "unknown"
    mtu: int = 1500
    link_type: str = "ethernet"  # "roce", "infiniband", "ethernet", "loopback", "virtual"
    rdma_device: str | None = None
    pci_address: str | None = None
    has_default_route: bool = False
    is_virtual: bool = False
    is_up: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ips"] = [ip.to_dict() for ip in self.ips]
        return data


def _read_sysfs_file(path: Path) -> str | None:
    """Read a stripped single-line sysfs file, returning None on error."""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except (OSError, PermissionError, UnicodeDecodeError):
        pass
    return None


def _parse_proc_net_route(proc_route_path: Path) -> tuple[str | None, str | None]:
    """Parse /proc/net/route to find the default gateway interface and IP.

    Returns:
        (interface_name, gateway_ip) or (None, None).
    """
    try:
        if not proc_route_path.is_file():
            return None, None
        content = proc_route_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 3:
                iface, dest_hex, gw_hex = parts[0], parts[1], parts[2]
                # Destination 00000000 is the default route
                if dest_hex == "00000000" and gw_hex != "00000000":
                    # Convert little-endian hex to dotted quad IPv4
                    try:
                        gw_bytes = struct.pack("<L", int(gw_hex, 16))
                        gateway_ip = socket.inet_ntoa(gw_bytes)
                        return iface, gateway_ip
                    except (ValueError, struct.error):
                        return iface, None
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Failed to parse %s: %s", proc_route_path, exc)
    return None, None


def _detect_rdma_device_for_net_if(
    if_path: Path,
    sys_ib_path: Path,
) -> tuple[str | None, str]:
    """Detect associated RDMA device and link type for a network interface.

    Returns:
        (rdma_device_name, link_type) where link_type is 'roce', 'infiniband', or 'ethernet'.
    """
    # 1. Check /sys/class/net/<if>/device/infiniband
    device_ib_dir = if_path / "device" / "infiniband"
    if device_ib_dir.is_dir():
        try:
            for entry in device_ib_dir.iterdir():
                rdma_name = entry.name
                # Check link_layer on port 1
                link_layer_file = entry / "ports" / "1" / "link_layer"
                link_layer = _read_sysfs_file(link_layer_file)
                if link_layer and link_layer.lower() == "infiniband":
                    return rdma_name, "infiniband"
                # If RoCE or Ethernet link layer
                return rdma_name, "roce"
        except (OSError, PermissionError):
            pass

    # 2. Check /sys/class/infiniband/<dev>/device/net/
    if sys_ib_path.is_dir():
        try:
            for ib_dev in sys_ib_path.iterdir():
                net_dir = ib_dev / "device" / "net"
                if net_dir.is_dir() and (net_dir / if_path.name).exists():
                    link_layer_file = ib_dev / "ports" / "1" / "link_layer"
                    link_layer = _read_sysfs_file(link_layer_file)
                    if link_layer and link_layer.lower() == "infiniband":
                        return ib_dev.name, "infiniband"
                    return ib_dev.name, "roce"
        except (OSError, PermissionError):
            pass

    return None, "ethernet"


def _is_virtual_interface(if_name: str, if_path: Path | None) -> bool:
    """Is *if_name* container/VM plumbing rather than a usable link?

    Two signals, both conservative: sysfs exposes a ``bridge`` directory for
    any bridge device, and the well-known container/VM name prefixes cover the
    rest (veth pairs, tap devices, overlay interfaces).  Anything else - a
    physical NIC, a VLAN on top of one, a bond - is reported as a real link and
    left for the planner to score.
    """
    if if_path is not None and (if_path / "bridge").is_dir():
        return True
    return if_name.lower().startswith(_VIRTUAL_NAME_PREFIXES)


def _is_ephemeral_interface(if_name: str) -> bool:
    """Is *if_name* a per-container device that churns on every start/stop?"""
    return if_name.lower().startswith(_EPHEMERAL_NAME_PREFIXES)


def collect_network_interfaces(
    *,
    sys_net_path: Path = DEFAULT_SYS_NET,
    sys_ib_path: Path = DEFAULT_SYS_IB,
    proc_route_path: Path = DEFAULT_PROC_ROUTE,
) -> list[NetworkInterfaceInfo]:
    """Collect all host network interfaces with hardware facts and addressing.

    Works on Linux via sysfs and falls back gracefully to psutil on non-Linux.
    """
    default_iface, default_gw = _parse_proc_net_route(proc_route_path)

    # Fetch addresses and stats via psutil
    try:
        psutil_addrs = psutil.net_if_addrs()
    except Exception:  # pragma: no cover
        psutil_addrs = {}

    try:
        psutil_stats = psutil.net_if_stats()
    except Exception:  # pragma: no cover
        psutil_stats = {}

    # Discover interface names from sysfs if available, otherwise from psutil
    if sys_net_path.is_dir():
        try:
            iface_names = sorted([entry.name for entry in sys_net_path.iterdir() if entry.is_dir() or entry.is_symlink()])
        except OSError:
            iface_names = sorted(list(psutil_addrs.keys()))
    else:
        iface_names = sorted(list(psutil_addrs.keys()))

    interfaces: list[NetworkInterfaceInfo] = []

    for if_name in iface_names:
        if_path = sys_net_path / if_name if sys_net_path.is_dir() else None

        # Basic identification
        mac: str | None = None
        operstate = "unknown"
        mtu = 1500
        speed_mbps: int | None = None
        pci_address: str | None = None
        rdma_device: str | None = None
        link_type = "ethernet"

        # Special case loopback
        if if_name == "lo":
            link_type = "loopback"
            operstate = "up"
        elif _is_virtual_interface(if_name, if_path):
            # Docker bridges, veth pairs, VLANs, bonds and tunnels are facts
            # too — they are reported, but typed so the planner can exclude
            # them from fabric candidacy (they carry identical RFC1918
            # addresses on every host and can never form a cluster).
            link_type = "virtual"

        if if_path and if_path.exists():
            # Operstate
            op = _read_sysfs_file(if_path / "operstate")
            if op:
                operstate = op.lower()

            # MTU
            mtu_str = _read_sysfs_file(if_path / "mtu")
            if mtu_str and mtu_str.isdigit():
                mtu = int(mtu_str)

            # Speed in Mb/s
            speed_str = _read_sysfs_file(if_path / "speed")
            if speed_str:
                try:
                    val = int(speed_str)
                    if val > 0:
                        speed_mbps = val
                except ValueError:
                    pass

            # MAC address
            addr_str = _read_sysfs_file(if_path / "address")
            if addr_str and addr_str != "00:00:00:00:00:00":
                mac = addr_str.lower()

            # PCI Address from device symlink
            device_symlink = if_path / "device"
            if device_symlink.exists():
                try:
                    resolved = device_symlink.resolve()
                    pci_address = resolved.name  # e.g. "0000:01:00.1"
                except OSError:
                    pass

            # RDMA device and link type (RoCE / IB)
            if link_type not in ("loopback", "virtual"):
                rdma_dev, determined_type = _detect_rdma_device_for_net_if(if_path, sys_ib_path)
                if rdma_dev:
                    rdma_device = rdma_dev
                    link_type = determined_type
        else:
            # Fallback to psutil stats
            stat = psutil_stats.get(if_name)
            if stat:
                operstate = "up" if stat.isup else "down"
                mtu = stat.mtu
                if stat.speed > 0:
                    speed_mbps = stat.speed

        # Address resolution from psutil
        ips: list[IPAddressInfo] = []
        for saddr in psutil_addrs.get(if_name, []):
            if saddr.family == socket.AF_INET:
                ip = saddr.address
                netmask = saddr.netmask
                cidr: str | None = None
                if netmask:
                    try:
                        network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                        cidr = str(network)
                    except ValueError:
                        pass
                ips.append(IPAddressInfo(ip=ip, netmask=netmask, cidr=cidr, family="ipv4"))
            elif hasattr(socket, "AF_INET6") and saddr.family == socket.AF_INET6:
                # Store non-link-local IPv6 or all IPv6
                ips.append(IPAddressInfo(ip=saddr.address.split("%")[0], netmask=saddr.netmask, family="ipv6"))
            elif saddr.family == psutil.AF_LINK and not mac:
                mac = saddr.address

        # Default route is a *fact* read from the routing table.  When the
        # table is unreadable (non-Linux, restricted container) it stays
        # False for every interface rather than being guessed from
        # interface-name ordering — a guess here would silently drive the
        # planner's management penalty on the wrong link.
        has_default_route = bool(default_iface) and if_name == default_iface

        is_up = operstate == "up" or (psutil_stats.get(if_name).isup if if_name in psutil_stats else False)
        speed_gbps = round(speed_mbps / 1000.0, 2) if speed_mbps is not None else None

        interfaces.append(
            NetworkInterfaceInfo(
                name=if_name,
                ips=ips,
                mac=mac,
                speed_mbps=speed_mbps,
                speed_gbps=speed_gbps,
                operstate=operstate,
                mtu=mtu,
                link_type=link_type,
                rdma_device=rdma_device,
                pci_address=pci_address,
                has_default_route=has_default_route,
                is_virtual=link_type == "virtual",
                is_up=is_up,
            )
        )

    return interfaces


def summarize_network_inventory(
    interfaces: list[NetworkInterfaceInfo],
) -> dict[str, Any]:
    """Build the normalized network summary for ``InfraNode.capabilities_json['network']``.

    Tier-1 (this summary) carries the planner-relevant facts for every
    non-loopback interface that has an IPv4 address, including kernel-virtual
    ones (typed ``link_type="virtual"``) so the planner can both see and
    explain why it rejected them.  ``default_route_interface`` is the routing
    fact; the *conclusion* (which link is "management") is the planner's.

    Tier-2 detail (``all_interfaces``) drops ephemeral per-container devices:
    a DGX head carries ~20 ``veth*`` devices that churn on every container
    start, and a snapshot row is written on every inventory tick.
    """
    fabrics: list[dict[str, Any]] = []
    default_route_interface: str | None = None
    default_route_ip: str | None = None

    for iface in interfaces:
        primary_ipv4 = next((a for a in iface.ips if a.family == "ipv4"), None)
        if iface.has_default_route and default_route_interface is None:
            default_route_interface = iface.name
            if primary_ipv4:
                default_route_ip = primary_ipv4.ip

        if primary_ipv4 and iface.link_type != "loopback":
            fabrics.append({
                "interface": iface.name,
                "ip": primary_ipv4.ip,
                "netmask": primary_ipv4.netmask,
                "cidr": primary_ipv4.cidr,
                "speed_gbps": iface.speed_gbps,
                "speed_mbps": iface.speed_mbps,
                "link_type": iface.link_type,
                "rdma_device": iface.rdma_device,
                "pci_address": iface.pci_address,
                "mtu": iface.mtu,
                "operstate": iface.operstate,
                "has_default_route": iface.has_default_route,
                "is_virtual": iface.is_virtual,
                "is_up": iface.is_up,
            })

    retained = [i for i in interfaces if not _is_ephemeral_interface(i.name)]
    return {
        "default_route_interface": default_route_interface,
        "default_route_ip": default_route_ip,
        # Kept for consumers written against the first cut of this payload.
        # It is the default-route address, i.e. still a fact, not a verdict.
        "management_ip": default_route_ip,
        "fabrics": fabrics,
        "all_interfaces": [i.to_dict() for i in retained],
        "omitted_ephemeral_interfaces": len(interfaces) - len(retained),
    }


def tier1_network_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Project the Tier-1 slice of a network summary (drops Tier-2 detail).

    This is what belongs on ``InfraNode.capabilities_json``; the full document
    (with ``all_interfaces``) stays in ``InfraNodeInventorySnapshot``.
    """
    return {k: v for k, v in (summary or {}).items() if k != "all_interfaces"}


async def handle_validate_fabric_listen(payload: dict[str, Any]) -> dict[str, Any]:
    """``VALIDATE_FABRIC_LISTEN`` handler: bind one short-lived probe listener.

    Bound to the *candidate interface's* address so the challenge certifies
    that specific fabric rather than whatever route the kernel would pick.
    ``expected_probes`` is the number of peers that will dial in - one per
    other member of the environment.
    """
    ip = str(payload.get("ip") or "")
    if not ip:
        return {"listening": False, "error": "ip is required"}
    port = int(payload.get("port") or 0)
    if not (0 < port < 65536):
        return {"listening": False, "error": f"invalid port {port}"}
    return await run_ephemeral_listener(
        ip,
        port,
        timeout_sec=float(payload.get("timeout_sec") or 5.0),
        probe_token=str(payload.get("probe_token") or ""),
        expected_probes=max(1, int(payload.get("expected_probes") or 1)),
    )


async def handle_validate_fabric_connect(payload: dict[str, Any]) -> dict[str, Any]:
    """``VALIDATE_FABRIC_CONNECT`` handler: probe a peer's ephemeral listener.

    Retries while the peer is merely not listening *yet*.  The listener and
    the probes are separate node commands dispatched over separate agent
    websockets, so their start order is not guaranteed; without this the
    challenge would fail whenever the prober happened to win the race.  A
    definitive answer (token rejected, handshake completed) is returned
    immediately.
    """
    target_ip = str(payload.get("target_ip") or "")
    if not target_ip:
        return {"reachable": False, "error": "target_ip is required"}
    port = int(payload.get("target_port") or 0)
    if not (0 < port < 65536):
        return {"reachable": False, "error": f"invalid target_port {port}"}

    timeout_sec = float(payload.get("timeout_sec") or 5.0)
    retry_for_sec = float(payload.get("retry_for_sec") or 0.0)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + retry_for_sec
    attempts = 0

    while True:
        attempts += 1
        result = await run_ephemeral_connect(
            target_ip,
            port,
            timeout_sec=timeout_sec,
            probe_token=str(payload.get("probe_token") or ""),
            source_ip=payload.get("source_ip") or None,
        )
        if result.get("reachable") or not _is_retryable_dial_error(result.get("error")):
            break
        if loop.time() >= deadline:
            break
        await asyncio.sleep(0.25)

    result.setdefault("target_ip", target_ip)
    result.setdefault("target_port", port)
    result["attempts"] = attempts
    return result


def _is_retryable_dial_error(error: str | None) -> bool:
    """Is *error* consistent with "the peer is not bound yet"?

    Linux refuses a dial to an unbound port immediately; Windows lets it hang
    until the connect timeout.  Both are ambiguous between "listener is still
    starting" and "fabric is down", so both are retried - the final answer is
    still an honest ``reachable: False`` once the window closes.  A definitive
    answer (token rejected) is never retried.
    """
    if not error:
        return False
    lowered = error.lower()
    return (
        "refused" in lowered
        or "timeout" in lowered
        or "10061" in lowered
        or "unreachable" in lowered
    )


async def run_ephemeral_listener(
    ip: str,
    port: int,
    timeout_sec: float = 5.0,
    probe_token: str = "",
    expected_probes: int = 1,
) -> dict[str, Any]:
    """Run an ephemeral TCP listener on a designated fabric IP for active validation.

    Accepts ``expected_probes`` probe connections (one per peer being
    certified), verifies each probe token, responds with ACK, and terminates as
    soon as they have all been served.  Never leaves dangling listening
    sockets: the server is closed on every exit path, including the timeout.
    """
    done = asyncio.Event()
    peers: list[Any] = []
    client_info: dict[str, Any] = {}

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout_sec)
            token = line.decode("utf-8").strip()
            if probe_token and token != probe_token:
                writer.write(b"ERR_AUTH\n")
            else:
                writer.write(b"ACK\n")
                peers.append(writer.get_extra_info("peername"))
            await writer.drain()
            client_info["peer"] = writer.get_extra_info("peername")
        except Exception as exc:  # pragma: no cover
            client_info["error"] = str(exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # pragma: no cover
                pass
            if len(peers) >= expected_probes:
                done.set()

    try:
        server = await asyncio.start_server(_handle_client, host=ip, port=port)
    except Exception as exc:
        detail = str(exc).strip()
        return {
            "listening": False,
            "error": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__,
        }

    async with server:
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_sec)
            return {
                "listening": True,
                "connected": True,
                "probes_accepted": len(peers),
                "expected_probes": expected_probes,
                "peer": client_info.get("peer"),
                "peers": peers,
            }
        except TimeoutError:
            return {
                "listening": True,
                "connected": False,
                "probes_accepted": len(peers),
                "expected_probes": expected_probes,
                "error": "timeout_waiting_for_probe",
            }


async def run_ephemeral_connect(
    target_ip: str,
    target_port: int,
    timeout_sec: float = 5.0,
    probe_token: str = "",
    source_ip: str | None = None,
) -> dict[str, Any]:
    """Connect to an ephemeral probe listener, measure RTT, and verify connectivity.

    ``source_ip`` pins the outgoing socket to the candidate fabric's local
    address.  Without it the kernel picks a route and the probe can certify a
    link the plan is not about.
    """
    start_time = asyncio.get_running_loop().time()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                target_ip,
                target_port,
                local_addr=(source_ip, 0) if source_ip else None,
            ),
            timeout=timeout_sec,
        )
        try:
            payload = (probe_token or "probe") + "\n"
            writer.write(payload.encode("utf-8"))
            await writer.drain()
            resp = await asyncio.wait_for(reader.readline(), timeout=timeout_sec)
            rtt_ms = (asyncio.get_running_loop().time() - start_time) * 1000.0
            if resp.strip() == b"ACK":
                return {"reachable": True, "rtt_ms": round(rtt_ms, 3)}
            return {
                "reachable": False,
                "error": f"probe rejected: {resp.decode('utf-8', errors='replace').strip()}",
            }
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # pragma: no cover
                pass
    except Exception as exc:
        # Always name the exception: a bare ConnectionRefusedError stringifies
        # to "" on some platforms, and this text is what the planner surfaces
        # as the blocker explaining why a fabric was refused.
        detail = str(exc).strip()
        return {
            "reachable": False,
            "error": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__,
        }

