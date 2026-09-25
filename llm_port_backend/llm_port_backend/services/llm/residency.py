"""Where a provider's prompts go: on our machines, our network, or outside.

The data residency map used to decide this from how a provider was added:
``local_docker`` was local and ``remote_endpoint`` was "cloud". So a vLLM
found running on one of our own enrolled machines -- routed as a remote
endpoint -- showed as cloud, as did any self-hosted server on the LAN, and
providers served by our own clusters were not counted at all.

Residency is decided from where the endpoint *is*, in this order:

1. An administrator's override, when there is one.
2. Ours by construction: a container on this host, a deployment on one of our
   clusters.
3. A cloud API by declaration: the ``cloud`` type, a LiteLLM provider, or a
   host that belongs to a known AI service (works with no network at all).
4. One of our machines: the endpoint's host is an enrolled machine's address.
5. DNS, resolved here -- the network the gateway calls from. Every address
   private (RFC 1918, loopback, link-local, IPv6 ULA, the 100.64/10 range
   Tailscale uses) or inside a network the operator declared internal: our
   network. Any public address: external. No answer: unknown, not a guess.

An address says where the endpoint is, not where the prompt ends up -- a proxy
on the LAN can forward it to a cloud API -- which is why the override exists
and why every answer carries its reason.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: On a machine LLM.Port runs or manages.
MACHINES = "machines"
#: Self-hosted on the organisation's own network.
PRIVATE = "private"
#: Leaves the organisation: a cloud API or a public address.
EXTERNAL = "external"
#: Could not be told from here.
UNKNOWN = "unknown"

KINDS = (MACHINES, PRIVATE, EXTERNAL, UNKNOWN)
OVERRIDES = (MACHINES, PRIVATE, EXTERNAL)

#: Hosts of hosted AI APIs, matched on the domain and its subdomains. A proxy
#: in front of one of these is caught by DNS or the override, not here.
KNOWN_CLOUD_DOMAINS = (
    "openai.com",
    "openai.azure.com",
    "services.ai.azure.com",
    "inference.ai.azure.com",
    "anthropic.com",
    "googleapis.com",
    "mistral.ai",
    "groq.com",
    "deepseek.com",
    "cohere.com",
    "cohere.ai",
    "openrouter.ai",
    "together.xyz",
    "together.ai",
    "fireworks.ai",
    "perplexity.ai",
    "x.ai",
    "amazonaws.com",
    "huggingface.co",
    "cerebras.ai",
    "deepinfra.com",
    "api.nvidia.com",
    "replicate.com",
    "ai21.com",
)

#: Carrier-grade NAT space: Tailscale's addresses, never routed on the internet.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

#: Names that always mean "this server".
_THIS_SERVER = {"localhost", "host.docker.internal", "gateway.docker.internal"}

_DNS_TTL_SEC = 300.0
_DNS_MISS_TTL_SEC = 60.0
_DNS_TIMEOUT_SEC = 2.0
_dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


@dataclass(frozen=True)
class Residency:
    """Where a provider's prompts go, and why that is the answer."""

    kind: str
    #: Why: override | managed | cloud_provider | cloud_host | machine |
    #: this_server | private_address | internal_network | public_address |
    #: unresolved | no_endpoint
    source: str
    host: str | None = None
    addresses: tuple[str, ...] = ()
    #: The enrolled machine the endpoint is on, for ``machine``.
    machine: str | None = None
    #: The cloud provider named, for ``cloud_provider``.
    provider: str | None = None

    @property
    def local(self) -> bool:
        """Stays on the organisation's own infrastructure."""
        return self.kind in (MACHINES, PRIVATE)

    def to_dict(self) -> dict[str, Any]:
        """The API shape (``ResidencyDTO``)."""
        return {
            "kind": self.kind,
            "source": self.source,
            "host": self.host,
            "addresses": list(self.addresses),
            "machine": self.machine,
            "provider": self.provider,
        }


@dataclass
class Context:
    """What the classification is checked against, gathered once per request."""

    #: address -> machine name, from enrolled machines.
    machine_addresses: dict[str, str] = field(default_factory=dict)
    #: Networks the operator counts as internal, public ranges included.
    internal_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = field(default_factory=list)
    #: host -> resolved addresses, filled by ``resolve_all``.
    resolved: dict[str, tuple[str, ...]] = field(default_factory=dict)


def parse_networks(text: str | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """``"203.0.113.0/24, 2001:db8::/32"`` -> networks; bad entries are skipped and logged."""
    out = []
    for entry in (text or "").replace(";", ",").split(","):
        part = entry.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("residency: %r is not a network; ignored", part)
    return out


def endpoint_host(url: str | None) -> str | None:
    """The host an endpoint URL names, lower-cased; None when there is none."""
    text = (url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"http://{text}"
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def is_known_cloud_host(host: str) -> bool:
    """*host* is, or is under, a cloud API domain in ``KNOWN_CLOUD_DOMAINS``."""
    return any(host == domain or host.endswith("." + domain) for domain in KNOWN_CLOUD_DOMAINS)


def _is_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or (isinstance(address, ipaddress.IPv4Address) and address in _SHARED_ADDRESS_SPACE)
    )


def machine_addresses(nodes: list[Any]) -> dict[str, str]:
    """Map each address to its machine's name, from each node's host and real interfaces.

    Virtual interfaces are left out: a Docker bridge is 172.17.0.1 on every
    machine, and matching it would name the wrong one.
    """
    out: dict[str, str] = {}
    for node in nodes:
        name = str(getattr(node, "agent_id", "") or getattr(node, "host", ""))
        host = str(getattr(node, "host", "") or "").strip().lower()
        if host:
            out.setdefault(host, name)
        network = (getattr(node, "capabilities_json", None) or {}).get("network") or {}
        for fabric in network.get("fabrics") or []:
            if not isinstance(fabric, dict) or fabric.get("is_virtual"):
                continue
            ip = str(fabric.get("ip") or "").strip().lower()
            if ip:
                out.setdefault(ip, name)
    return out


async def _resolve(host: str) -> tuple[str, ...]:
    now = time.monotonic()
    cached = _dns_cache.get(host)
    if cached and cached[0] > now:
        return cached[1]
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=_DNS_TIMEOUT_SEC,
        )
        addresses = tuple(sorted({str(info[4][0]).split("%", 1)[0] for info in infos}))
    except (OSError, TimeoutError, UnicodeError):
        addresses = ()
    _dns_cache[host] = (now + (_DNS_TTL_SEC if addresses else _DNS_MISS_TTL_SEC), addresses)
    return addresses


async def resolve_all(hosts: set[str], ctx: Context) -> None:
    """Resolve every host that needs DNS, together, into ``ctx.resolved``."""
    wanted = sorted(h for h in hosts if h and h not in ctx.resolved)
    if not wanted:
        return
    results = await asyncio.gather(*(_resolve(h) for h in wanted))
    ctx.resolved.update(zip(wanted, results, strict=True))


def needs_dns(provider: Any) -> str | None:
    """The host to resolve for *provider*, when steps 1-4 cannot decide it."""
    if getattr(provider, "residency_override", None) in OVERRIDES:
        return None
    if _target(provider) != "remote_endpoint" or _declared_cloud(provider):
        return None
    host = endpoint_host(getattr(provider, "endpoint_url", None))
    if not host or host in _THIS_SERVER or is_known_cloud_host(host):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None  # an IP literal needs no lookup


def _target(provider: Any) -> str:
    target = getattr(provider, "target", "")
    return str(getattr(target, "value", target) or "")


def _declared_cloud(provider: Any) -> str | None:
    """The cloud provider a provider declares itself to be, if it does."""
    litellm = (getattr(provider, "litellm_provider", None) or "").strip()
    if litellm:
        return litellm
    ptype = getattr(provider, "type", "")
    if str(getattr(ptype, "value", ptype)) == "cloud":
        return "cloud"
    return None


def classify(provider: Any, ctx: Context) -> Residency:
    """Where *provider*'s prompts go. Pure: DNS must already be in ``ctx.resolved``.

    The rules are tried in order: what an administrator said, what we run, a
    declared cloud API, then where the endpoint's host is.
    """
    host = endpoint_host(getattr(provider, "endpoint_url", None))
    declared = _declared(provider, host)
    if declared:
        return declared
    if not host:
        return Residency(UNKNOWN, "no_endpoint")
    if is_known_cloud_host(host):
        return Residency(EXTERNAL, "cloud_host", host=host)
    if host in _THIS_SERVER:
        return Residency(MACHINES, "this_server", host=host)
    return _by_address(host, ctx)


def _declared(provider: Any, host: str | None) -> Residency | None:
    """What is settled without looking at an address, if anything is."""
    override = getattr(provider, "residency_override", None)
    if override in OVERRIDES:
        return Residency(override, "override", host=host)
    if _target(provider) in ("local_docker", "inference_cluster"):
        return Residency(MACHINES, "managed", host=host)
    cloud = _declared_cloud(provider)
    if cloud:
        return Residency(EXTERNAL, "cloud_provider", host=host, provider=cloud)
    return None


def _by_address(host: str, ctx: Context) -> Residency:
    """Where the addresses *host* stands for are: one of our machines, our network, or outside."""
    try:
        addresses: tuple[str, ...] = (str(ipaddress.ip_address(host)),)
    except ValueError:
        addresses = ctx.resolved.get(host, ())

    for candidate in (host, *addresses):
        machine = ctx.machine_addresses.get(candidate)
        if machine:
            return Residency(MACHINES, "machine", host=host, addresses=addresses, machine=machine)
    if not addresses:
        return Residency(UNKNOWN, "unresolved", host=host)

    parsed = [ipaddress.ip_address(a) for a in addresses]
    if all(_is_private(a) for a in parsed):
        if all(a.is_loopback for a in parsed):
            return Residency(MACHINES, "this_server", host=host, addresses=addresses)
        return Residency(PRIVATE, "private_address", host=host, addresses=addresses)
    if all(_is_private(a) or any(a in net for net in ctx.internal_networks) for a in parsed):
        return Residency(PRIVATE, "internal_network", host=host, addresses=addresses)
    return Residency(EXTERNAL, "public_address", host=host, addresses=addresses)


async def classify_all(providers: list[Any], ctx: Context) -> dict[str, Residency]:
    """Map each provider id to its residency, resolving what needs DNS together first."""
    await resolve_all({h for p in providers if (h := needs_dns(p))}, ctx)
    return {str(p.id): classify(p, ctx) for p in providers}


def clear_cache() -> None:
    """Forget resolved hosts (tests)."""
    _dns_cache.clear()
