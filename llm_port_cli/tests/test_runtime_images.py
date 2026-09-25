"""deploy and upgrade put the runtime images on the server; machines download them from it.

Nothing did before: a machine gets its runtime image from the server's own
Docker (or a cluster peer), and a fresh install had none to give -- it could
take over a cluster that already had its image, and could not equip a new
machine. The images a release pins are read out of its backend image, so the
CLI fetches exactly what the backend will hand out.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from llmport.commands import runtime_images as cmd
from llmport.core import runtime_images as rt
from llmport.core.settings import LlmportConfig

GB10 = "ghcr.io/llm-port/ray-runtime-gb10:2.58.0-1"
X86 = "ghcr.io/llm-port/ray-runtime-x86_64:2.58.0-1"
GB10_LAYERS = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]
X86_LAYERS = ["sha256:" + "3" * 64]

MANIFESTS = {
    # The GB10 manifest names its architecture under target_hardware; the
    # x86_64 one as "machine" -- both shapes are in the repository.
    "runtime-manifest.json": {"release_tag": GB10, "rootfs_layers": GB10_LAYERS,
                              "target_hardware": {"architecture": "aarch64"}},
    "runtime-manifest-x86_64.json": {"release_tag": X86, "rootfs_layers": X86_LAYERS, "machine": "x86_64"},
}


class FakeDocker:
    """The docker commands the module runs, answered from a small image store."""

    def __init__(
        self,
        store: dict[str, list[str]] | None = None,
        *,
        registry: dict[str, list[str]] | None = None,
        manifests: dict[str, Any] | None = None,
        pull_fails: bool = False,
    ) -> None:
        self.store = dict(store or {})
        self.registry = registry if registry is not None else {GB10: GB10_LAYERS, X86: X86_LAYERS}
        self.manifests = MANIFESTS if manifests is None else manifests
        self.pull_fails = pull_fails
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        """Answer one docker command."""
        self.calls.append(argv)
        verb = argv[1]
        if verb == "run":
            out = "some log line\n" + json.dumps(self.manifests) + "\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if verb == "image":
            ref = argv[-1]
            if ref not in self.store:
                return subprocess.CompletedProcess(argv, 1, "", f"No such image: {ref}")
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.store[ref]), "")
        if verb == "pull":
            ref = argv[-1]
            if self.pull_fails or ref not in self.registry:
                return subprocess.CompletedProcess(argv, 1, "", "denied")
            self.store[ref] = self.registry[ref]
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    def pulls(self) -> list[list[str]]:
        """The pull commands run so far."""
        return [c for c in self.calls if c[1] == "pull"]


@pytest.fixture()
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(rt, "_run", fake)
    monkeypatch.setattr(rt.shutil, "which", lambda name: "docker")
    return fake


def _use(monkeypatch: pytest.MonkeyPatch, fake: FakeDocker) -> FakeDocker:
    monkeypatch.setattr(rt, "_run", fake)
    return fake


def test_the_images_come_from_the_backend_image_of_this_release(
    docker: FakeDocker, tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text("VERSION=0.3.0\nREGISTRY=ghcr.io/llm-port\n", encoding="utf-8")
    image = rt.backend_image(env)
    assert image == "ghcr.io/llm-port/backend:0.3.0"

    pinned = {p.ref: p for p in rt.pinned(image)}
    assert set(pinned) == {GB10, X86}
    assert (pinned[GB10].arch, pinned[GB10].platform) == ("aarch64", "linux/arm64")
    assert (pinned[X86].arch, pinned[X86].platform) == ("x86_64", "linux/amd64")
    assert docker.calls[0][:5] == ["docker", "run", "--rm", "--entrypoint", "python"]


def test_an_install_without_a_version_pin_reads_latest(tmp_path: Path) -> None:
    assert rt.backend_image(tmp_path / ".env") == "ghcr.io/llm-port/backend:latest"


def test_missing_images_are_pulled_for_their_own_platform(docker: FakeDocker) -> None:
    report = rt.fetch("backend:x", architectures={"x86_64", "aarch64"})
    assert report.ok
    assert sorted(o.status for o in report.outcomes) == ["pulled", "pulled"]
    # An x86_64 server that serves DGX machines holds the arm64 image too.
    assert ["docker", "pull", "--platform", "linux/arm64", GB10] in docker.pulls()
    assert ["docker", "pull", "--platform", "linux/amd64", X86] in docker.pulls()


def test_an_image_already_here_is_not_pulled_again(
    monkeypatch: pytest.MonkeyPatch, docker: FakeDocker,
) -> None:
    fake = _use(monkeypatch, FakeDocker({GB10: GB10_LAYERS, X86: X86_LAYERS}))
    report = rt.fetch("backend:x", architectures={"x86_64", "aarch64"})
    assert [o.status for o in report.outcomes] == ["present", "present"]
    assert fake.pulls() == []


def test_only_the_chosen_architectures_are_fetched(docker: FakeDocker) -> None:
    report = rt.fetch("backend:x", architectures={"x86_64"})
    by_ref = {o.image.ref: o.status for o in report.outcomes}
    assert by_ref == {GB10: "skipped", X86: "pulled"}
    assert [c[-1] for c in docker.pulls()] == [X86]


def test_a_build_on_no_registry_is_not_asked_of_docker_hub(
    monkeypatch: pytest.MonkeyPatch, docker: FakeDocker,
) -> None:
    """Before a runtime release is certified the manifest names a local build."""
    local = {"runtime-manifest.json": {"release_tag": "llmport/ray-vllm-gb10:ray2.58-nv26.08",
                                       "rootfs_layers": GB10_LAYERS, "base_image": {"architecture": "arm64"}}}
    fake = _use(monkeypatch, FakeDocker(manifests=local))
    report = rt.fetch("backend:x", architectures={"aarch64"})
    assert [o.status for o in report.outcomes] == ["not_published"]
    assert fake.pulls() == []


def test_a_refused_pull_is_reported_not_fatal(
    monkeypatch: pytest.MonkeyPatch, docker: FakeDocker,
) -> None:
    _use(monkeypatch, FakeDocker(pull_fails=True))
    report = rt.fetch("backend:x", architectures={"x86_64"})
    assert [o.status for o in report.outcomes if o.image.ref == X86] == ["failed"]
    assert not report.ok


def test_a_moved_tag_is_caught(monkeypatch: pytest.MonkeyPatch, docker: FakeDocker) -> None:
    """The registry serves another build under the tag: machines would refuse it."""
    _use(monkeypatch, FakeDocker(registry={X86: ["sha256:" + "9" * 64]}))
    report = rt.fetch("backend:x", architectures={"x86_64"})
    assert [o.status for o in report.outcomes if o.image.ref == X86] == ["mismatch"]


def test_a_backend_image_without_manifests_says_so(
    monkeypatch: pytest.MonkeyPatch, docker: FakeDocker,
) -> None:
    _use(monkeypatch, FakeDocker(manifests={}))
    report = rt.fetch("backend:x", architectures={"x86_64"})
    assert "names no runtime images" in report.error


@pytest.mark.parametrize(("ref", "registry"), [
    (GB10, True),
    ("localhost:5000/ray-runtime:1", True),
    ("llmport/ray-vllm-gb10:ray2.58-nv26.08", False),
    ("llmport-ray-runtime-x86_64:2.58.0", False),
])
def test_what_counts_as_a_registry(ref: str, registry: bool) -> None:
    assert rt.is_registry_ref(ref) is registry


def test_the_choice_is_read_and_checked() -> None:
    assert rt.parse_selection("all") == {"x86_64", "aarch64"}
    assert rt.parse_selection(None) == {"x86_64", "aarch64"}
    assert rt.parse_selection("none") == set()
    assert rt.parse_selection("amd64, arm64") == {"x86_64", "aarch64"}
    with pytest.raises(ValueError, match="riscv64"):
        rt.parse_selection("riscv64")


def test_a_choice_on_the_command_line_is_remembered_for_upgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[str] = []
    monkeypatch.setattr(cmd, "save_config", lambda c: saved.append(c.runtime_images))
    cfg = LlmportConfig()
    assert cmd.choose(cfg, "x86_64") == {"x86_64"}
    assert saved == ["x86_64"] and cfg.runtime_images == "x86_64"
    assert cmd.choose(cfg, None) == {"x86_64"}, "the next upgrade uses it"
    assert cmd.choose(cfg, "sparc") is None


def test_none_fetches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_docker(*_: Any, **__: Any) -> None:
        raise AssertionError("docker must not run")

    monkeypatch.setattr(rt, "fetch", no_docker)
    assert cmd.run_step(LlmportConfig(runtime_images="none")) is True
