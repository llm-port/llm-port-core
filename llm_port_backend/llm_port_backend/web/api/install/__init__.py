"""The installer the operator actually runs, assembled by the backend.

Why this is a generated endpoint rather than a file in the repo: the whole
point of R-1 is that nothing is assembled by the human.  The script that lands
on the node already knows where this backend is, which architecture it is
running on, which agent version to fetch and what that binary should hash to.
None of those are things the operator should have to look up, and a checksum
in particular is the worst possible thing to ask a person to verify by eye --
so the script verifies it instead.

On ``curl | bash``: we deliberately do not.  The neighbouring products pipe a
script from a third-party host straight into a root shell, which is how they
get a one-line install.  Here the script lands on disk first, so it can be
read before it is run, and it verifies what it downloads.  The trust anchor is
"you are talking to your own backend", which the operator is doing anyway --
it is the machine holding all their credentials.

Binary source: the plan offers both the published release and this backend's
own copy, and the *node* picks by trying them in order.  Deciding here was
wrong on its face -- whether there is internet is a fact about the node, not
about this server, so one answer had to serve an air-gapped rack and a
connected office alike.

Either way the digest comes from here, so the verification is identical and
the source really is the only difference.  That is also what makes preferring
the release safe: a download that does not match the build this deployment
expects is refused, wherever it came from.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
from pathlib import Path

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from yarl import URL

from llm_port_backend.settings import DEFAULT_AGENT_BINARY_DIR, settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/install", tags=["install"])

#: Agent version this backend expects.  Bumping it here is what makes a fleet
#: converge, because every fresh install reads it from the running backend.
AGENT_VERSION = "0.1.11"

#: Where published binaries live when this backend has no local copy.
RELEASE_URL = (
    "https://github.com/llm-port/llm-port-node-agent/releases/download"
    "/v{version}/llmport-agent-{platform}"
)

#: Platforms we publish, keyed as ``uname -s``/``uname -m`` reports them.
PLATFORMS = {
    "linux-aarch64": "linux-aarch64",
    "linux-arm64": "linux-aarch64",
    "linux-x86_64": "linux-x86_64",
    "linux-amd64": "linux-x86_64",
    "darwin-arm64": "macos-universal",
    "darwin-x86_64": "macos-universal",
}


def _source_checkout_binary_dir() -> Path | None:
    """The agent's own build output, when this backend runs from a checkout.

    ``sh build-binary.sh`` writes into ``llm_port_node_agent/dist/``.  That
    used to have to be copied into ``agent_binary_dir`` by hand: two copies
    of a 14MB binary, and a standing chore to keep them identical that
    nothing enforced -- so the one the backend served drifted behind the one
    that was built, silently, because both files exist and neither says
    which is current.

    Reading the build output directly means there is one file.  An installed
    backend has no checkout above it, so this finds nothing there.
    """
    candidate = Path(__file__).resolve().parents[5] / "llm_port_node_agent" / "dist"
    return candidate if candidate.is_dir() else None


def _local_binary_dirs() -> list[Path]:
    """Where to look for a local agent copy, in priority order.

    An operator who set ``agent_binary_dir`` gets exactly that directory:
    they placed those binaries deliberately, and a checkout that happens to
    be on the same disk must not quietly outrank them.  Left at the default,
    a checkout's ``dist/`` comes first instead -- running from source, the
    binary you just built is the one you meant.
    """
    configured = Path(settings.agent_binary_dir)
    if settings.agent_binary_dir != DEFAULT_AGENT_BINARY_DIR:
        return [configured]

    checkout = _source_checkout_binary_dir()
    return [checkout, configured] if checkout else [configured]


def local_binary(platform: str) -> Path | None:
    """The local copy for *platform*, when this deployment carries one."""
    for directory in _local_binary_dirs():
        candidate = directory / f"llmport-agent-{platform}"
        if candidate.is_file():
            return candidate
    return None


def binary_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _origin(request: Request) -> str:
    """This backend, as the caller reached it.

    Taken from the request rather than configuration precisely because the
    node has to reach the same address the operator's browser did.  A
    configured value would be right in exactly the deployments that do not
    need this endpoint.
    """
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_host:
        return f"{forwarded_proto or request.url.scheme}://{forwarded_host}"
    return str(request.base_url).rstrip("/")


def _is_loopback(host: str) -> bool:
    """Whether *host* only ever means "this machine".

    Splitting on ``:`` to drop a port is wrong for IPv6, where the colons are
    the address: ``::1`` came back as not-loopback, which is exactly backwards
    and would have let the console hand out ``http://[::1]:8000``.
    """
    bare = host.strip().lower()
    if bare.startswith("["):  # [::1]:8000
        bare = bare[1:].split("]", 1)[0]
    elif bare.count(":") == 1:  # host:port
        bare = bare.split(":", 1)[0]
    if bare == "localhost":
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def reachable_origins() -> list[dict[str, str]]:
    """Addresses of this backend that another machine could actually use.

    Read from the host's own interfaces rather than from the request. The
    operator's browser is frequently somewhere the node is not -- at
    ``localhost:5173`` in front of a dev proxy, or through an SSH tunnel --
    and the console was handing out that address as the one to install
    against. A machine told to fetch the agent from ``localhost`` fetches it
    from itself and finds nothing there.
    """
    port = settings.port
    found: list[dict[str, str]] = []
    try:
        for name, addresses in psutil.net_if_addrs().items():
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                host = address.address
                if _is_loopback(host) or host.startswith("169.254."):
                    continue
                found.append({"url": f"http://{host}:{port}", "interface": name})
    except Exception:  # noqa: BLE001 - never block onboarding on inventory
        return []

    # A workstation has a pile of these -- VirtualBox host-only, WSL's
    # vEthernet, Docker's bridge, a VPN's CGNAT range -- and every one of them
    # is an address the node cannot use. None can be ruled out from here, so
    # order them and let the operator see which is which rather than picking
    # one silently and being wrong.
    def rank(entry: dict[str, str]) -> tuple[int, str]:
        label = entry["interface"].lower()
        virtual = any(
            token in label
            for token in ("virtualbox", "host-only", "vethernet", "docker", "vmware", "loopback", "tailscale")
        )
        cgnat = entry["url"].startswith("http://100.")
        return (1 if (virtual or cgnat) else 0, entry["url"])

    return sorted(found, key=rank)


@router.get("/address")
async def install_address(request: Request) -> dict[str, object]:
    """Where a machine being added should be pointed.

    The console used to build its install command from the browser's own
    URL, on the assumption that the operator and the node see the backend
    the same way. They frequently do not, and the failure is silent until
    someone runs the command on a real machine.
    """
    seen_as = _origin(request)
    candidates = reachable_origins()
    usable = not _is_loopback(URL(seen_as).host or "")
    return {
        # Prefer what the caller used when it can work; it accounts for
        # reverse proxies and real hostnames, which interfaces cannot.
        "url": seen_as if usable else (candidates[0]["url"] if candidates else seen_as),
        "seen_as": seen_as,
        "candidates": candidates,
        "request_origin_is_reachable": usable,
    }


def render_installer(origin: str) -> str:
    """The POSIX-sh installer, with this deployment's facts already in it."""
    # Written as plain sh, not bash: a DGX OS image is not guaranteed to have
    # anything more, and the script has to run before we have installed
    # anything at all.
    return f"""#!/bin/sh
# LLM.Port node agent installer.
#
# Generated by {origin} for that backend specifically -- the address, the
# agent version and the expected digest are already filled in, so there is
# nothing here for you to look up.
#
# It downloads the agent, checks the digest it was told to expect, installs
# it, and then either asks to join (and waits for someone to approve in the
# browser) or enrolls with a token you passed.
#
# Usage:
#   sudo sh llmport-agent.sh --join [{origin}]     system service
#   sh llmport-agent.sh --join [{origin}]          no sudo: user service
#   sh llmport-agent.sh --backend {origin} --token <token>
#
# Run it as root and the agent becomes a system service. Run it as yourself,
# without passwordless sudo, and it installs into ~/.local/bin and runs as a
# systemd user service -- no password asked for at any point.

set -eu

BACKEND="{origin}"
AGENT_VERSION="{AGENT_VERSION}"
MODE="join"
TOKEN=""
INSTALL_PATH="/usr/local/bin/llmport-agent"
INSTALL_PATH_SET=0
FORCE_USER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --join)
      MODE="join"
      if [ $# -gt 1 ] && [ "${{2#--}}" = "$2" ]; then BACKEND="$2"; shift; fi
      ;;
    --backend) BACKEND="$2"; shift ;;
    --token) MODE="token"; TOKEN="$2"; shift ;;
    --install-path) INSTALL_PATH="$2"; INSTALL_PATH_SET=1; shift ;;
    --user) FORCE_USER=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# -- with what privilege ----------------------------------------------------
# Decided once, up front, rather than discovered halfway: a run that could not
# get root used to install the binary and then die writing the service unit,
# leaving an agent that ran only while somebody's shell stayed open.
ROOTLESS=0
if [ "$(id -u)" -ne 0 ]; then
  if [ "$FORCE_USER" = 1 ] || ! sudo -n true 2>/dev/null; then
    ROOTLESS=1
    if [ "$INSTALL_PATH_SET" = 0 ]; then
      INSTALL_PATH="$HOME/.local/bin/llmport-agent"
    fi
    echo "  No root here, so installing for $(id -un) only: no password needed."
    echo
  fi
fi

# -- which build do we need -------------------------------------------------
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
PLATFORM_KEY="${{OS}}-${{ARCH}}"

echo "  Machine:  $PLATFORM_KEY"
echo "  LLM.Port: $BACKEND"
echo

# The backend knows which of its builds fits this machine, and what that build
# should hash to.  Asking it beats making the operator choose.
PLAN=$(curl -fsSL "$BACKEND/api/install/plan?platform=$PLATFORM_KEY") || {{
  echo "ERROR: could not reach $BACKEND" >&2
  exit 1
}}

# JSON is allowed to escape forward slashes, and this backend's encoder does:
# every URL arrives as "https:\\/\\/host\\/path". Pulling one out with sed and
# handing it to curl gave "URL using bad/illegal format" -- from both sources,
# so the installer could not fetch the agent at all.
PLAN=$(printf '%s' "$PLAN" | sed 's|\\\\/|/|g')

URL=$(printf '%s' "$PLAN" | sed -n 's/.*"url"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
FALLBACK=$(printf '%s' "$PLAN" | sed -n 's/.*"fallback_url"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
SHA=$(printf '%s' "$PLAN" | sed -n 's/.*"sha256"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')

if [ -z "$URL" ]; then
  echo "ERROR: this backend publishes no agent build for $PLATFORM_KEY." >&2
  echo "       $PLAN" >&2
  exit 1
fi

# -- fetch and verify -------------------------------------------------------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "  Downloading the agent..."
if ! curl -fsSL -o "$TMP/llmport-agent" "$URL"; then
  if [ -n "$FALLBACK" ]; then
    # No internet here, or the release is unreachable. LLM.Port carries its
    # own copy, and it is a machine this node can obviously reach -- it is
    # the one it is about to enrol with.
    echo "  Could not reach $URL"
    echo "  Falling back to LLM.Port's own copy..."
    curl -fsSL -o "$TMP/llmport-agent" "$FALLBACK" || {{
      echo "ERROR: could not download the agent from either source." >&2
      exit 1
    }}
  else
    echo "ERROR: could not download the agent from $URL" >&2
    echo "       This LLM.Port carries no local copy to fall back to." >&2
    exit 1
  fi
fi

if [ -n "$SHA" ]; then
  echo "  Checking it is the build this backend expects..."
  ACTUAL=$(sha256sum "$TMP/llmport-agent" 2>/dev/null | cut -d' ' -f1) \\
    || ACTUAL=$(shasum -a 256 "$TMP/llmport-agent" | cut -d' ' -f1)
  if [ "$ACTUAL" != "$SHA" ]; then
    echo "ERROR: the download does not match the expected digest." >&2
    echo "       expected $SHA" >&2
    echo "       got      $ACTUAL" >&2
    exit 1
  fi
  echo "  Verified."
else
  # Say so rather than implying a check happened.  A missing digest is a
  # deployment that has not published one, not a passed verification.
  echo "  NOTE: this backend published no digest, so nothing was verified."
fi

# -- install ----------------------------------------------------------------
# Two forms, because the enrol path below passes the configuration through the
# environment: sudo needs -E to keep it, and running as root must not be handed
# a bare "-E" to execute. It was, and every token enrolment run as root -- the
# ordinary case for an installer -- died on "-E: not found" after the agent had
# already been installed, so it looked like the agent was broken.
#
# Which of the two is needed depends on where the agent is going. Installing
# into /usr/local/bin needs root; installing into a directory this user
# already owns does not, and asking for a password to write a file you own is
# how a rootless install failed on a machine whose sudo needs one.
INSTALL_DIR=$(dirname "$INSTALL_PATH")
mkdir -p "$INSTALL_DIR" 2>/dev/null || true
if [ "$(id -u)" -eq 0 ] || [ "$ROOTLESS" = 1 ]; then
  SUDO=""
  SUDO_E=""
elif [ -w "$INSTALL_DIR" ]; then
  SUDO=""
  # A system service still needs root whatever the binary's path.
  SUDO_E="sudo -E"
else
  SUDO="sudo"
  SUDO_E="sudo -E"
fi
$SUDO install -m 0755 "$TMP/llmport-agent" "$INSTALL_PATH"
echo "  Installed $INSTALL_PATH"
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  # Silence here means "command not found" on the very next line of the
  # instructions, for a file that installed perfectly well.
  *) echo "  NOTE: $INSTALL_DIR is not on your PATH." ;;
esac
echo

# -- join or enroll ---------------------------------------------------------
# Without root the service is a systemd user service, which needs none.
SCOPE=""
if [ "$ROOTLESS" = 1 ]; then
  SCOPE="--user"
fi
if [ "$MODE" = "token" ]; then
  LLM_PORT_NODE_AGENT_BACKEND_URL="$BACKEND" \\
  LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN="$TOKEN" \\
    $SUDO_E "$INSTALL_PATH" start $SCOPE
else
  "$INSTALL_PATH" join $SCOPE "$BACKEND"
fi
"""


@router.get("/llmport-agent.sh", response_class=PlainTextResponse)
async def installer_script(request: Request) -> PlainTextResponse:
    """The installer, generated for this backend.

    Unauthenticated: it contains no secret.  Everything it knows is the
    address the caller already reached and which builds exist.
    """
    return PlainTextResponse(
        render_installer(_origin(request)),
        media_type="text/x-shellscript",
        headers={"Content-Disposition": 'attachment; filename="llmport-agent.sh"'},
    )


@router.get("/plan")
async def install_plan(request: Request, platform: str = "linux-x86_64") -> dict[str, str | None]:
    """Where to get the agent for *platform*, and what it should hash to.

    Answering this here is what lets the script stay dumb and the operator
    stay uninvolved: the backend decides between its own copy and the
    published release, and the digest it returns is checked either way.
    """
    build = PLATFORMS.get(platform.strip().lower())
    if build is None:
        return {
            "platform": platform,
            "url": None,
            "sha256": None,
            "reason": f"No agent build is published for {platform}.",
        }

    local = local_binary(build)
    release_url = RELEASE_URL.format(version=AGENT_VERSION, platform=build)

    if local is None:
        # Nothing shipped with this deployment, so the release is the only
        # source -- and we cannot state a digest for bytes we have never
        # seen. The script says so rather than implying a check happened.
        return {
            "platform": build,
            "url": release_url,
            "fallback_url": None,
            "sha256": None,
            "source": "release",
        }

    # Both are available, and the node picks by trying. Whether there is
    # internet is a fact about the *node*, not about this server, so deciding
    # here gave one answer to an air-gapped rack and a connected office alike.
    #
    # The release goes first because it is a CDN and this is somebody's
    # server; the fallback is what makes the same command work with no
    # internet at all.
    #
    # The digest is this backend's own copy either way. A release that does
    # not match it is not the build this deployment expects and the script
    # refuses it -- which is the point of pinning: the source stops mattering.
    return {
        "platform": build,
        "url": release_url,
        "fallback_url": f"{_origin(request)}/api/install/binary/{build}",
        "sha256": binary_digest(local),
        "source": "release+backend",
    }


@router.get("/binary/{build}")
async def install_binary(build: str) -> Response:
    """Serve this deployment's own copy of the agent, when it has one."""
    if build not in set(PLATFORMS.values()):
        return Response(status_code=404)
    local = local_binary(build)
    if local is None:
        return Response(status_code=404)
    return Response(
        content=local.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="llmport-agent-{build}"'},
    )
