"""Map a semantic accelerator request onto one container runtime's flags.

``ContainerRequirements.gpus`` is deliberately semantic -- "all", "none", or a
count -- because the bundle should say *what the runtime needs*, not how one
vendor's container toolkit spells it.  That contract was only half kept: the
docker handler turned every request into ``--gpus``, which is the NVIDIA
container toolkit's flag and means nothing to ROCm.

Keeping the translation here, keyed by vendor, is what lets an AMD or Intel
node join without touching the bundle format, the agent protocol, or the
scheduler.  A backend that does not containerise its accelerators at all --
a peer-to-peer runtime on Apple silicon, say -- simply asks for no flags.
"""

from __future__ import annotations

_NONE = ("", "none", "0")

#: CDI vendor prefixes, for runtimes that expose devices through the Container
#: Device Interface rather than a vendor-specific flag.
_CDI_PREFIX = {
    "nvidia": "nvidia.com/gpu",
    "cuda": "nvidia.com/gpu",
    "amd": "amd.com/gpu",
    "rocm": "amd.com/gpu",
    "intel": "intel.com/gpu",
    "xpu": "intel.com/gpu",
    "level_zero": "intel.com/gpu",
}


def _requested(gpus: str | None) -> str | None:
    """The semantic request, or ``None`` when nothing should be exposed."""
    if gpus is None or str(gpus).strip().lower() in _NONE:
        return None
    return str(gpus).strip()


def _vendor(vendor: str | None) -> str:
    """Normalised vendor key.  ``None`` means NVIDIA, as every bundle does today."""
    return (vendor or "nvidia").strip().lower()


def accelerator_run_flags(vendor: str | None, gpus: str | None) -> list[str]:
    """Container-runtime flags that expose *gpus* of *vendor* to a container.

    Args:
        vendor: ``nvidia`` / ``amd`` / ``intel`` / ``apple``, as the agent's
            GPU collectors report it.  ``None`` is treated as NVIDIA, which is
            what every existing bundle means.
        gpus: the semantic request: ``"all"``, ``"none"``/``""``, or a count.

    Returns:
        The flags to append, or an empty list when nothing should be exposed.
    """
    request = _requested(gpus)
    if request is None:
        return []
    name = _vendor(vendor)

    if name in ("nvidia", "cuda"):
        # The NVIDIA container toolkit understands "all" and "device=..." here.
        return ["--gpus", request]

    if name in ("amd", "rocm"):
        # ROCm exposes the kernel fusion driver and the render nodes instead;
        # there is no count syntax, so any non-"none" request means "the GPUs
        # this container is allowed to see".
        return [
            "--device=/dev/kfd",
            "--device=/dev/dri",
            "--group-add=video",
            "--security-opt", "seccomp=unconfined",
        ]

    if name in ("intel", "xpu", "level_zero"):
        # Intel GPUs are exposed through the render nodes alone.
        return ["--device=/dev/dri", "--group-add=video"]

    if name in ("apple", "metal"):
        # Metal is not reachable from a Linux container; a backend targeting
        # Apple silicon runs on the host rather than behind this flag.
        return []

    # An accelerator we have no mapping for: ask for nothing rather than
    # guess a flag that would fail at container start with a runtime error.
    return []


def accelerator_cdi_flags(vendor: str | None, gpus: str | None) -> list[str]:
    """The same request, for a runtime that exposes devices through CDI.

    Podman takes ``--device <vendor>.com/gpu=...`` instead of the NVIDIA
    toolkit's ``--gpus``, so the vendor lives in the device name itself.  That
    is the whole reason this is a second function rather than a flag: the two
    runtimes disagree about *where* the vendor goes, not just how it is spelt.

    Args:
        vendor: as for :func:`accelerator_run_flags`.
        gpus: ``"all"``, ``"none"``/``""``, or a count.

    Returns:
        The flags to append, or an empty list when nothing should be exposed.
    """
    request = _requested(gpus)
    if request is None:
        return []

    prefix = _CDI_PREFIX.get(_vendor(vendor))
    if prefix is None:
        # Apple/Metal and anything unmapped: no CDI device to ask for.
        return []

    if request.isdigit():
        # CDI names one device at a time, so a count becomes that many indices.
        flags: list[str] = []
        for index in range(int(request)):
            flags.extend(["--device", f"{prefix}={index}"])
        return flags

    return ["--device", f"{prefix}={request}"]
