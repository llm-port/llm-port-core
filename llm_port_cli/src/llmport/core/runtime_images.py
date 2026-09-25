"""Runtime images: the containers models run in, fetched onto the server.

A machine never pulls its runtime image from the internet. The LLM.Port
server exports the pinned image from its own Docker and the machine downloads
it from the server, or from a cluster peer that already has it. So the server
has to hold every runtime image a machine may ask for -- and nothing put it
there: a fresh install could take over a cluster that already had its image,
and could not give a new machine one.

``deploy`` and ``upgrade`` now fetch them from the registry the release
publishes to (``ghcr.io/llm-port/ray-runtime-*``). Which images is not decided
here: they are read out of this release's backend image, from the same
runtime manifests the backend builds its catalogue from, so the CLI can never
fetch a build the backend would then refuse.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from llmport.core.env_gen import read_env_file

#: Architectures as machines report them (``uname -m``), with the names
#: registries and Docker use for the same thing.
ARCHITECTURES = ("x86_64", "aarch64")
_ARCH_ALIASES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
_PLATFORMS = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}

#: Run inside the backend image, whose working directory holds the manifests.
_READ_MANIFESTS = (
    "import glob, json\n"
    "print(json.dumps({p.rsplit('/', 1)[-1]: json.load(open(p))"
    " for p in sorted(glob.glob('llm_port_runtime_image/runtime-manifest*.json'))}))\n"
)

DEFAULT_REGISTRY = "ghcr.io/llm-port"


@dataclass(frozen=True)
class RuntimeImage:
    """One runtime image a release pins."""

    manifest: str
    ref: str
    arch: str
    rootfs_layers: tuple[str, ...]

    @property
    def platform(self) -> str | None:
        """Docker's name for the architecture (``linux/arm64``), or None when unknown."""
        return _PLATFORMS.get(self.arch)

    @property
    def published(self) -> bool:
        """Whether the reference names a registry to pull it from.

        Until a runtime release is certified, a manifest can still name a
        build that only ever existed on the machines it was made on
        (``llmport/ray-vllm-gb10:…``); pulling that would ask Docker Hub for
        an image nobody published.
        """
        return is_registry_ref(self.ref)


@dataclass
class Outcome:
    """What became of one image."""

    image: RuntimeImage
    status: str  # present | pulled | skipped | not_published | failed | mismatch
    detail: str = ""


@dataclass
class Report:
    """Everything ``fetch`` did, for the caller to print."""

    backend_image: str
    outcomes: list[Outcome] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether everything that was wanted is on the server."""
        return not self.error and not any(o.status in {"failed", "mismatch"} for o in self.outcomes)


def is_registry_ref(ref: str) -> bool:
    """``ghcr.io/org/name:tag`` yes; ``org/name:tag`` or ``name:tag`` no (they mean Docker Hub)."""
    first, sep, _ = ref.partition("/")
    return bool(sep) and ("." in first or ":" in first or first == "localhost")


def normalise_arch(value: str) -> str:
    """``amd64`` -> ``x86_64``, ``arm64`` -> ``aarch64``; empty for anything else."""
    return _ARCH_ALIASES.get((value or "").strip().lower(), "")


def parse_selection(value: str | None) -> set[str]:
    """``all`` / ``none`` / ``x86_64,aarch64`` -> the architectures to fetch.

    Raises:
        ValueError: an architecture nobody builds a runtime for.
    """
    text = (value or "all").strip().lower()
    if text in {"", "all"}:
        return set(ARCHITECTURES)
    if text == "none":
        return set()
    chosen: set[str] = set()
    for part in text.split(","):
        arch = normalise_arch(part)
        if not arch:
            raise ValueError(f"unknown architecture {part.strip()!r}; use all, none, x86_64 or aarch64")
        chosen.add(arch)
    return chosen


def backend_image(env_path: Path) -> str:
    """The backend image this install runs, as the compose file names it."""
    env = read_env_file(env_path) if env_path.exists() else {}
    registry = (env.get("REGISTRY") or DEFAULT_REGISTRY).rstrip("/")
    version = env.get("VERSION") or "latest"
    return f"{registry}/backend:{version}"


def _arch_of(manifest: dict) -> str:
    for value in (
        manifest.get("machine"),
        (manifest.get("target_hardware") or {}).get("architecture"),
        (manifest.get("base_image") or {}).get("architecture"),
    ):
        arch = normalise_arch(str(value or ""))
        if arch:
            return arch
    return ""


def _docker() -> str:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("docker is not on PATH")
    return docker


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, **kwargs)  # noqa: S603


def pinned(image: str) -> list[RuntimeImage]:
    """The runtime images *image* (a backend image) pins, read out of it.

    Raises:
        RuntimeError: the backend image cannot be run or holds no manifests.
    """
    proc = _run(
        [_docker(), "run", "--rm", "--entrypoint", "python", image, "-c", _READ_MANIFESTS],
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"could not read the runtime manifests from {image}: {detail[0]}")
    manifests: dict[str, dict] = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            manifests = json.loads(line)
            break
    images = []
    for name, manifest in manifests.items():
        ref = str(manifest.get("release_tag") or manifest.get("target_image") or "")
        if not ref:
            continue
        images.append(
            RuntimeImage(
                manifest=name,
                ref=ref,
                arch=_arch_of(manifest),
                rootfs_layers=tuple(manifest.get("rootfs_layers") or ()),
            )
        )
    return images


def local_layers(ref: str) -> tuple[str, ...] | None:
    """The layers of *ref* in this server's Docker, or None when it is not here."""
    proc = _run(
        [_docker(), "image", "inspect", "--format", "{{json .RootFS.Layers}}", ref],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return tuple(json.loads(proc.stdout.strip() or "[]"))
    except ValueError:
        return None


def pull(image: RuntimeImage) -> int:
    """``docker pull`` it, for its own platform, showing Docker's progress."""
    cmd = [_docker(), "pull"]
    if image.platform:
        # The GB10 image exists only for arm64. An x86_64 server that hands it
        # to DGX machines has to be told which platform it wants, or Docker
        # finds "no matching manifest" for its own.
        cmd += ["--platform", image.platform]
    cmd.append(image.ref)
    return _run(cmd).returncode


def fetch(image: str, *, architectures: set[str]) -> Report:
    """Put every runtime image *image* pins, for the chosen architectures, on this server."""
    report = Report(backend_image=image)
    try:
        images = pinned(image)
    except RuntimeError as exc:
        report.error = str(exc)
        return report
    if not images:
        report.error = f"{image} names no runtime images"
        return report

    for runtime in images:
        if runtime.arch and runtime.arch not in architectures:
            report.outcomes.append(Outcome(runtime, "skipped", f"{runtime.arch} not chosen"))
            continue
        # Identity is the content, as the backend checks it: the layer list.
        have = local_layers(runtime.ref)
        if have is not None and have == runtime.rootfs_layers:
            report.outcomes.append(Outcome(runtime, "present"))
            continue
        if not runtime.published:
            report.outcomes.append(
                Outcome(runtime, "not_published", "names no registry; load it with docker load"),
            )
            continue
        if pull(runtime) != 0:
            report.outcomes.append(Outcome(runtime, "failed", "docker pull failed"))
            continue
        have = local_layers(runtime.ref)
        if runtime.rootfs_layers and have != runtime.rootfs_layers:
            # The tag was moved, or the registry serves another build: the
            # backend would refuse to hand this to a machine.
            report.outcomes.append(
                Outcome(runtime, "mismatch", "the registry's image is not the build this release pins"),
            )
            continue
        report.outcomes.append(Outcome(runtime, "pulled"))
    return report
