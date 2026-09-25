#!/usr/bin/env python3
"""Build a runtime image and emit its manifest, from the image itself.

Two images, one per platform (see the README):

    gb10     Dockerfile           aarch64, NVIDIA DGX Spark (GB10)
    x86_64   Dockerfile.x86_64    generic x86_64 NVIDIA, Turing to Blackwell

Runs anywhere that can produce the image's platform: natively (a DGX node or
an arm64 CI runner for ``gb10``, any x86_64 machine for ``x86_64``) or through
``docker buildx`` with binfmt/QEMU (``--platform``). No step needs a GPU:
nothing compiles against the local CUDA stack, and the build-time assertions
only *execute* the target architecture, which emulation handles.

What still requires the hardware is **certification**, not the build. A build
asserts that the image is well-formed; a certification run on real machines is
what earns ``overall_status``. So every manifest written here says
``uncertified``, whatever the previous one said.

Why this script exists
----------------------
The deployed image once drifted from every record of it: the manifest named a
build that existed on neither DGX node, and the two nodes held byte-identical
content under different config IDs. The cause was not the build -- it was that
identity was **transcribed by hand** into places that then disagreed.

So nothing is transcribed. The manifest carries ``rootfs_layers`` (the RootFS
layer diff IDs, which survive a save/load transfer and a registry round trip)
and the backend derives the content digest from them through
``RuntimeBundleManifest.from_runtime_manifest``. The digest algorithm lives in
exactly one place -- ``llm_port_backend.services.inference.bundles`` -- and is
deliberately *not* reimplemented here.

Stack versions come from the helper inside the finished image
(``llm-port-ray-runtime versions``), the same source the agent and the
``system_fingerprint`` use. The architectures the image's torch was compiled
for are read from torch itself, without a GPU (``get_arch_list`` returns
nothing on a machine that has none, which is every CI runner).

Usage
-----
    python3 rebuild_runtime_image.py --build                          # gb10, on a DGX node
    python3 rebuild_runtime_image.py --flavor x86_64 --build          # on any x86_64 box
    python3 rebuild_runtime_image.py --build --platform linux/arm64   # gb10, off-node
    python3 rebuild_runtime_image.py --build --distribute sachi@10.88.10.71
    python3 rebuild_runtime_image.py --verify-peer sachi@10.88.10.71

    # CI (.github/workflows/runtime-image-release.yml):
    python3 rebuild_runtime_image.py --flavor x86_64 --build --push \\
        --tag ghcr.io/llm-port/ray-runtime-x86_64:2.58.0-1

``--verify-peer`` re-reads both nodes' layer lists and fails if they differ,
which is the check that would have caught the original drift.
"""

from __future__ import annotations

import argparse
import json
import pprint
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Flavor:
    """One runtime image: how it is built and where its manifest goes."""

    dockerfile: str
    manifest: str
    default_tag: str
    runtime_id: str
    hardware_target: str
    #: The catalogue entry in bundles.py this manifest feeds (for the hint).
    bundle: str


FLAVORS: dict[str, Flavor] = {
    "gb10": Flavor(
        dockerfile="Dockerfile",
        manifest="runtime-manifest.json",
        default_tag="llmport/ray-vllm-gb10:ray2.58-nv26.08",
        runtime_id="llmport-ray-vllm-gb10-ray2.58-nv26.08",
        hardware_target="NVIDIA DGX Spark (GB10) pair",
        bundle="bundle-dgx-spark-gb10-v1",
    ),
    "x86_64": Flavor(
        dockerfile="Dockerfile.x86_64",
        manifest="runtime-manifest-x86_64.json",
        default_tag="llmport-ray-runtime-x86_64:2.58.0",
        runtime_id="llmport-ray-vllm-x86_64-ray2.58",
        hardware_target="generic x86_64 NVIDIA",
        bundle="bundle-generic-x86_64-nvidia-v1",
    ),
}

#: Read inside the image, with no GPU: the SM architectures torch was built for
#: and the machine it runs on. ``torch.cuda.get_arch_list()`` would say [] on a
#: runner without a GPU, so this asks the compiled-in flags directly.
_ARCH_PROBE = (
    "import json, platform, torch\n"
    "flags = torch._C._cuda_getArchFlags() if hasattr(torch._C, '_cuda_getArchFlags') else ''\n"
    "print(json.dumps({'machine': platform.machine(), 'arch_list': (flags or '').split()}))\n"
)


class BuildError(RuntimeError):
    """A step failed; the message carries the command output."""


def _run(cmd: list[str], *, capture: bool = True) -> str:
    """Run a command, raising :class:`BuildError` with its output on failure."""
    proc = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise BuildError(f"{' '.join(cmd)} failed ({proc.returncode}): {detail}")
    return (proc.stdout or "").strip()


def _ssh(target: str, remote_cmd: str) -> str:
    """Run one command on *target* over ssh."""
    return _run(["ssh", target, remote_cmd])


def _json_from(raw: str, what: str) -> dict:
    """The JSON document in a command's output, tolerating log lines around it."""
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    start = raw.find("{")
    if start < 0:
        raise BuildError(f"{what} produced no JSON: {raw[:200]}")
    return json.loads(raw[start:])


def build(tag: str, flavor: Flavor, *, no_cache: bool, platform: str | None = None) -> None:
    """Build the image from its Dockerfile in this directory.

    The Dockerfiles assert both the metrics import path and the helper's verb
    surface, so a build that would strand a node fails here instead of on a
    node at deploy time.

    *platform* selects a cross build through ``buildx`` with binfmt/QEMU;
    ``--load`` puts the result in the local image store, which is what
    ``inspect_image`` reads.
    """
    if platform:
        cmd = ["docker", "buildx", "build", "--platform", platform, "--load", "-t", tag]
    else:
        cmd = ["docker", "build", "-t", tag]
    cmd += ["-f", str(HERE / flavor.dockerfile)]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(HERE))
    print(f"==> docker build {tag} ({flavor.dockerfile})")
    subprocess.run(cmd, check=True)


def push(tag: str) -> None:
    """Push to the registry the tag names, so the manifest can record its digest."""
    print(f"==> docker push {tag}")
    subprocess.run(["docker", "push", tag], check=True)


def inspect_image(tag: str) -> dict:
    """Read the identity fields off the built image."""
    raw = _run(["docker", "image", "inspect", tag])
    data = json.loads(raw)
    if not data:
        raise BuildError(f"docker image inspect returned nothing for {tag}")
    entry = data[0]
    layers = list((entry.get("RootFS") or {}).get("Layers") or [])
    if not layers:
        raise BuildError(f"{tag} reports no RootFS layers; cannot establish identity")
    # The digest of *this* reference, when it has been pushed or pulled; a
    # side-loaded image has none, which the backend accepts.
    repository = tag.rsplit(":", 1)[0] if ":" in tag.rsplit("/", 1)[-1] else tag
    repo_digest = None
    for ref in entry.get("RepoDigests") or []:
        name, _, digest = ref.partition("@")
        if name == repository:
            repo_digest = digest
            break
    return {
        "image_id": entry.get("Id"),
        "rootfs_layers": layers,
        "repo_digest": repo_digest,
        "architecture": entry.get("Architecture"),
        "os": entry.get("Os"),
    }


def read_stack_versions(tag: str, *, platform: str | None = None) -> dict:
    """Ask the helper inside the image what it actually has.

    One source for the versions, so the bundle catalog and the helper's own
    report cannot disagree. Under emulation the helper runs slower but reports
    the same thing.
    """
    cmd = ["docker", "run", "--rm"]
    if platform:
        cmd += ["--platform", platform]
    cmd += ["--entrypoint", "llm-port-ray-runtime", tag, "versions"]
    report = _json_from(_run(cmd), "helper 'versions'")
    return {key: value for key, value in report.items() if value is not None}


def read_arch(tag: str, *, platform: str | None = None) -> dict:
    """The SM architectures the image's torch was compiled for, as capabilities too."""
    cmd = ["docker", "run", "--rm"]
    if platform:
        cmd += ["--platform", platform]
    cmd += ["--entrypoint", "python3", tag, "-c", _ARCH_PROBE]
    facts = _json_from(_run(cmd), "architecture probe")
    arch_list = [a for a in facts.get("arch_list") or [] if a.startswith("sm_")]
    # sm_75 -> "7.5", sm_121a -> "12.1": the form the node agent reports.
    # NVIDIA's builds name family-specific targets with a suffix.
    numbers = {m.group(1) for a in arch_list if (m := re.match(r"sm_(\d{2,3})", a))}
    caps = sorted(
        {f"{n[:-1]}.{n[-1]}" for n in numbers},
        key=lambda c: tuple(int(part) for part in c.split(".")),
    )
    return {"machine": facts.get("machine"), "arch_list": arch_list, "compute_capabilities": caps}


def remote_layers(target: str, tag: str) -> list[str]:
    """Read a peer node's RootFS layers for the same tag."""
    out = _ssh(
        target,
        "docker image inspect --format '{{json .RootFS.Layers}}' " + shlex.quote(tag),
    )
    return list(json.loads(out))


def distribute(tag: str, target: str) -> None:
    """Stream the image to a peer node.

    ``docker save | ssh docker load`` rather than a registry push: a site's
    nodes may be air-gapped, and this is the transfer under which the config
    ID changes but the layer diff IDs do not -- which is exactly why identity
    is based on the latter.
    """
    print(f"==> transferring {tag} to {target}")
    save = subprocess.Popen(["docker", "save", tag], stdout=subprocess.PIPE)
    load = subprocess.Popen(["ssh", target, "docker load"], stdin=save.stdout)
    if save.stdout is not None:
        save.stdout.close()
    load.communicate()
    save.wait()
    if load.returncode != 0 or save.wait() != 0:
        raise BuildError(f"transfer to {target} failed")


def verify_peer(tag: str, target: str, local_layers: list[str]) -> None:
    """Fail unless the peer holds byte-identical content.

    Comparing the layer lists directly means this check never needs the
    backend's digest function, so there is no second implementation of it to
    drift.
    """
    peer = remote_layers(target, tag)
    if peer != local_layers:
        raise BuildError(
            f"{target} holds different content for {tag}: "
            f"{len(peer)} layers vs {len(local_layers)} locally, lists differ. "
            "Re-run with --distribute."
        )
    print(f"==> {target} holds identical content ({len(peer)} layers)")


def write_manifest(
    path: Path, tag: str, flavor: Flavor, identity: dict, stack: dict, arch: dict, previous: dict,
) -> dict:
    """Write the manifest with generated identity.

    ``rootfs_digest`` is deliberately absent: the backend computes it from
    ``rootfs_layers``. Writing both would reintroduce the transcription this
    script exists to remove.
    """
    manifest = {
        "schema_version": "1.1.0",
        "runtime_id": previous.get("runtime_id", flavor.runtime_id),
        "release_tag": tag,
        "image_id": identity["image_id"],
        "rootfs_layers": identity["rootfs_layers"],
        "repo_digest": identity.get("repo_digest"),
        # What the image is built on and for carries over; it is not read
        # from the image (the base's digest is not recorded in it).
        "base_image": previous.get("base_image", {}),
        "target_hardware": previous.get("target_hardware", {}),
        "stack_components": stack,
        "machine": arch.get("machine") or identity.get("architecture"),
        "arch_list": arch.get("arch_list", []),
        "compute_capabilities": arch.get("compute_capabilities", []),
        # Certification is evidence from a run, not from a build, so none of
        # it survives a rebuild. Only what the image is *for* carries over.
        #
        # This used to spread the previous certification and override the
        # status, which carried the old run's checks, counts and timestamp
        # along with it: a manifest reading "uncertified" and "11/11 checks
        # passed" at once, the passes describing a run against a different
        # image.
        "certification": {
            "hardware_target": (previous.get("certification") or {}).get("hardware_target")
            or flavor.hardware_target,
            "overall_status": "uncertified",
            "checks": [],
            "checks_passed": 0,
            "checks_total": 0,
            "timestamp": None,
            "detail": "built; certify it on its hardware before the catalogue points at it",
        },
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"==> wrote {path}")
    return manifest


def print_summary(manifest: dict, flavor: Flavor) -> None:
    """What was minted, in the terms the catalogue uses."""
    summary = {
        "bundle": flavor.bundle,
        "release_tag": manifest["release_tag"],
        "image_id": manifest["image_id"],
        "repo_digest": manifest["repo_digest"],
        "layers": len(manifest["rootfs_layers"]),
        "stack_components": manifest["stack_components"],
        "compute_capabilities": manifest["compute_capabilities"],
    }
    print("\n==> minted:")
    print(pprint.pformat(summary, indent=4, width=100, sort_dicts=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--flavor", choices=sorted(FLAVORS), default="gb10", help="which runtime image (default gb10)")
    parser.add_argument("--tag", help="image reference to build/inspect (default: the flavor's local tag)")
    parser.add_argument("--build", action="store_true", help="run docker build first")
    parser.add_argument("--no-cache", action="store_true", help="build without the layer cache")
    parser.add_argument(
        "--platform",
        metavar="OS/ARCH",
        help="cross-build through buildx for this platform (e.g. linux/arm64); omit for a native build",
    )
    parser.add_argument("--push", action="store_true", help="push the tag and record its registry digest")
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        help="where to write the manifest (default: the flavor's manifest in this directory)",
    )
    parser.add_argument(
        "--distribute",
        metavar="USER@HOST",
        help="stream the built image to this peer node and verify it landed identical",
    )
    parser.add_argument(
        "--verify-peer",
        metavar="USER@HOST",
        help="only compare this node's layers against a peer's, then exit",
    )
    args = parser.parse_args(argv)
    flavor = FLAVORS[args.flavor]
    tag = args.tag or flavor.default_tag
    manifest_path = Path(args.manifest) if args.manifest else HERE / flavor.manifest

    try:
        if args.verify_peer and not args.build:
            identity = inspect_image(tag)
            verify_peer(tag, args.verify_peer, identity["rootfs_layers"])
            return 0

        if args.build:
            build(tag, flavor, no_cache=args.no_cache, platform=args.platform)
        if args.push:
            push(tag)

        identity = inspect_image(tag)
        stack = read_stack_versions(tag, platform=args.platform)
        arch = read_arch(tag, platform=args.platform)
        previous = {}
        committed = HERE / flavor.manifest
        if committed.exists():
            try:
                previous = json.loads(committed.read_text(encoding="utf-8"))
            except ValueError:
                previous = {}

        manifest = write_manifest(manifest_path, tag, flavor, identity, stack, arch, previous)

        if args.distribute:
            distribute(tag, args.distribute)
            verify_peer(tag, args.distribute, identity["rootfs_layers"])

        print_summary(manifest, flavor)
        print(
            f"\n==> next: certify it on its hardware, then commit {flavor.manifest}; "
            f"the backend's catalogue ({flavor.bundle}) reads it."
        )
        return 0
    except (BuildError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
