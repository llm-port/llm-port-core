#!/usr/bin/env python3
"""Rebuild the certified runtime image and emit its manifest (Phase 4B blocker B-1).

Runs anywhere that can produce ``linux/arm64`` images -- a DGX node natively,
or a workstation with ``docker buildx`` and binfmt/QEMU (``--platform
linux/arm64``, which is what ``--platform`` below sets).

The image is the **official NVIDIA vLLM image plus one thin layer**: pip
installs and the ``llm_port_ray_runtime`` helper. Nothing in it compiles
against the local CUDA stack and no step needs a GPU, so hardware is not a
build requirement. The build-time assertions do *execute* aarch64 (importing
the metrics stack, running the helper's verbs), which emulation handles.

What still requires the hardware is **certification**, not the build: two GB10
nodes and the 200 Gb/s link. A build asserts that the image is well-formed; the
certification run is what earns ``overall_status``.

Why this script exists
----------------------
The deployed image drifted from every record of it: ``runtime-manifest.json``
named a build that exists on neither node, ``build_report.json`` named one that
exists only on the head, and the two nodes held byte-identical content under
different config IDs. The cause was not the build -- it was that identity was
**transcribed by hand** into three places that then disagreed.

So this script does not transcribe anything. It emits ``rootfs_layers`` (the
RootFS layer diff IDs, which are what actually survives a save/load transfer)
and lets the backend derive the content digest from them through
``RuntimeBundleManifest.from_runtime_manifest``. The digest algorithm lives in
exactly one place -- ``llm_port_backend.services.inference.bundles`` -- and is
deliberately *not* reimplemented here.

Stack versions come from the helper inside the finished image
(``llm-port-ray-runtime versions``), which is the same source the agent and the
``system_fingerprint`` use. Reading them any other way is how the manifest came
to record ``vllm 0.27.1+93523f72.dev`` while the bundle catalog recorded
``0.27.1+93523f72.nv26.8.64249418`` -- both real, from two different APIs.

Usage
-----
    python3 rebuild_runtime_image.py --build                       # on a DGX node
    python3 rebuild_runtime_image.py --build --platform linux/arm64  # off-node
    python3 rebuild_runtime_image.py --build --distribute sachi@10.88.10.71
    python3 rebuild_runtime_image.py --verify-peer sachi@10.88.10.71

The last form re-reads both nodes' layer lists and fails if they differ, which
is the check that would have caught the original drift.
"""

from __future__ import annotations

import argparse
import json
import pprint
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TAG = "llmport/ray-vllm-gb10:ray2.58-nv26.08"
MANIFEST_PATH = HERE / "runtime-manifest.json"


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


def build(tag: str, *, no_cache: bool, platform: str | None = None) -> None:
    """Build the image from the Dockerfile in this directory.

    The Dockerfile asserts both the metrics import path and the helper's verb
    surface, so a build that would strand a node fails here instead of on a
    node at deploy time.

    *platform* selects a cross build: off a DGX node, pass ``linux/arm64`` and
    the build runs through ``buildx`` with binfmt/QEMU. ``--load`` puts the
    result in the local image store, which is what ``inspect_image`` reads.
    """
    if platform:
        cmd = ["docker", "buildx", "build", "--platform", platform, "--load", "-t", tag]
    else:
        cmd = ["docker", "build", "-t", tag]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(HERE))
    print(f"==> docker build {tag}")
    subprocess.run(cmd, check=True)


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
    repo_digests = entry.get("RepoDigests") or []
    return {
        "image_id": entry.get("Id"),
        "rootfs_layers": layers,
        "repo_digest": repo_digests[0].split("@", 1)[-1] if repo_digests else None,
        "architecture": entry.get("Architecture"),
        "os": entry.get("Os"),
    }


def read_stack_versions(tag: str, *, platform: str | None = None) -> dict:
    """Ask the helper inside the image what it actually has.

    One source for the versions, so the bundle catalog and the helper's own
    report cannot disagree.  On a cross build the helper runs under emulation,
    which is slower but reports the same thing.
    """
    cmd = ["docker", "run", "--rm"]
    if platform:
        cmd += ["--platform", platform]
    cmd += ["--entrypoint", "llm-port-ray-runtime", tag, "versions"]
    raw = _run(cmd)
    # The helper prints a JSON document; tolerate leading log noise.
    start = raw.find("{")
    if start < 0:
        raise BuildError(f"helper 'versions' produced no JSON: {raw[:200]}")
    report = json.loads(raw[start:])
    return {key: value for key, value in report.items() if value is not None}


def remote_layers(target: str, tag: str) -> list[str]:
    """Read a peer node's RootFS layers for the same tag."""
    out = _ssh(
        target,
        "docker image inspect --format '{{json .RootFS.Layers}}' " + shlex.quote(tag),
    )
    return list(json.loads(out))


def distribute(tag: str, target: str) -> None:
    """Stream the image to a peer node.

    ``docker save | ssh docker load`` rather than a registry push: the pair is
    air-gapped, and this is the transfer under which the config ID changes but
    the layer diff IDs do not -- which is exactly why identity is based on the
    latter.
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


def write_manifest(tag: str, identity: dict, stack: dict, previous: dict) -> dict:
    """Write ``runtime-manifest.json`` with generated identity.

    ``rootfs_digest`` is deliberately absent: the backend computes it from
    ``rootfs_layers``. Writing both would reintroduce the transcription this
    script exists to remove.
    """
    manifest = {
        "schema_version": "1.1.0",
        "runtime_id": previous.get("runtime_id", "llmport-ray-vllm-gb10-ray2.58-nv26.08"),
        "release_tag": tag,
        "image_id": identity["image_id"],
        "rootfs_layers": identity["rootfs_layers"],
        "repo_digest": identity.get("repo_digest"),
        "base_image": previous.get("base_image", {}),
        "target_hardware": previous.get("target_hardware", {}),
        "stack_components": stack,
        # Certification is evidence from a run, not from a build, so none of
        # it survives a rebuild. Only what the image is *for* carries over.
        #
        # This used to spread the previous certification and override the
        # status, which carried the old run's checks, counts and timestamp
        # along with it. The result was a manifest reading "uncertified" and
        # "11/11 checks passed" at once, the passes describing a token
        # generation and a metrics scrape performed against a different
        # image. Anything that displays the counts shows a proof that was
        # never run.
        "certification": {
            "hardware_target": (previous.get("certification") or {}).get("hardware_target"),
            "overall_status": "uncertified",
            "checks": [],
            "checks_passed": 0,
            "checks_total": 0,
            "timestamp": None,
            "detail": "rebuilt; re-run remote_certify_2node.py to certify this image",
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"==> wrote {MANIFEST_PATH}")
    return manifest


def print_bundle_block(manifest: dict) -> None:
    """Print the catalog entry to paste into ``bundles.py``.

    Generated text, not a hand transcription: the layer list travels with it so
    ``from_runtime_manifest`` derives the content digest itself.
    """
    block = {
        "release_tag": manifest["release_tag"],
        "image_id": manifest["image_id"],
        "rootfs_layers": manifest["rootfs_layers"],
        "stack_components": manifest["stack_components"],
        "certification": manifest["certification"],
    }
    print(
        "\n==> paste into llm_port_backend/services/inference/bundles.py as\n"
        "    _CERTIFIED_DGX_SPARK_RUNTIME_MANIFEST (rootfs_digest is derived,\n"
        "    so do not add one):\n"
    )
    print(pprint.pformat(block, indent=4, width=100, sort_dicts=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=DEFAULT_TAG, help="image tag to build/inspect")
    parser.add_argument("--build", action="store_true", help="run docker build first")
    parser.add_argument("--no-cache", action="store_true", help="build without the layer cache")
    parser.add_argument(
        "--platform",
        metavar="OS/ARCH",
        help=(
            "cross-build through buildx for this platform (e.g. linux/arm64). "
            "Omit on a DGX node, where the build is native."
        ),
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

    try:
        if args.verify_peer and not args.build:
            identity = inspect_image(args.tag)
            verify_peer(args.tag, args.verify_peer, identity["rootfs_layers"])
            return 0

        if args.build:
            build(args.tag, no_cache=args.no_cache, platform=args.platform)

        identity = inspect_image(args.tag)
        stack = read_stack_versions(args.tag, platform=args.platform)
        previous = {}
        if MANIFEST_PATH.exists():
            try:
                previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            except ValueError:
                previous = {}

        manifest = write_manifest(args.tag, identity, stack, previous)

        if args.distribute:
            distribute(args.tag, args.distribute)
            verify_peer(args.tag, args.distribute, identity["rootfs_layers"])

        print_bundle_block(manifest)
        print(
            "\n==> next: re-run remote_certify_2node.py, then update the bundle "
            "catalog with the block above."
        )
        return 0
    except (BuildError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
