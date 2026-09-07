"""Unit tests for LLM provider adapters and the adapter registry.

These tests lock down:
* which adapters are *real* (vLLM) vs *stub* (ollama / tgi / llamacpp / cloud
  container build is NotImplementedError), so a future "we implemented ollama"
  change trips a test instead of silently passing.
* the vLLM adapter's pure `validate_model` and `build_container_spec` logic
  (GPU vendor → image selection, config → CLI flag mapping, engine_args
  passthrough, CPU fallback) with GPU detection mocked out.
* the registry `get_adapter` dispatch (enum + string keys, missing → error).

No infrastructure (DB/RabbitMQ) is required.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from llm_port_backend.db.models.llm import ArtifactFormat, ProviderType
from llm_port_backend.services.gpu.detector import detect_gpus
from llm_port_backend.services.gpu.types import (
    GpuComputeApi,
    GpuDevice,
    GpuInventory,
    GpuVendor,
)
from llm_port_backend.services.llm.adapters import vllm as vllm_adapter_module
from llm_port_backend.services.llm.adapters.cloud import CloudAdapter
from llm_port_backend.services.llm.adapters.llamacpp import LlamaCppAdapter
from llm_port_backend.services.llm.adapters.ollama import OllamaAdapter
from llm_port_backend.services.llm.adapters.tgi import TGIAdapter
from llm_port_backend.services.llm.adapters.vllm import VLLMAdapter
from llm_port_backend.services.llm import registry
from llm_port_backend.services.llm.base import CompatResult
from llm_port_backend.settings import settings

# ──────────────────────────────────────────────────────────────────────────────
# fixtures — plain objects exposing only the attributes the adapter reads
# ──────────────────────────────────────────────────────────────────────────────


def _inventory(
    vendor: GpuVendor,
    *,
    has_gpu: bool = True,
    compute_api: GpuComputeApi | None = None,
) -> GpuInventory:
    if not has_gpu:
        return GpuInventory(devices=[], primary_vendor=vendor, primary_compute_api=compute_api or GpuComputeApi.CPU)
    device = GpuDevice(index=0, vendor=vendor, model="Test GPU", compute_api=compute_api or GpuComputeApi.CUDA)
    return GpuInventory(devices=[device], primary_vendor=vendor, primary_compute_api=compute_api or GpuComputeApi.CUDA)


def _make_runtime(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "my-runtime",
        "generic_config": {},
        "provider_config": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_provider(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {"id": uuid.uuid4(), "type": ProviderType.VLLM}
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_model(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {"id": uuid.uuid4(), "hf_repo_id": "org/model"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _artifact(fmt: ArtifactFormat) -> SimpleNamespace:
    return SimpleNamespace(format=fmt)


def _build(
    monkeypatch: pytest.MonkeyPatch,
    inventory: GpuInventory,
    *,
    generic: dict | None = None,
    provider_cfg: dict | None = None,
) -> object:
    monkeypatch.setattr(vllm_adapter_module, "detect_gpus", lambda: inventory)
    runtime = _make_runtime(generic_config=generic or {}, provider_config=provider_cfg or {})
    return VLLMAdapter().build_container_spec(
        runtime=runtime,  # type: ignore[arg-type]
        provider=_make_provider(),  # type: ignore[arg-type]
        model=_make_model(),  # type: ignore[arg-type]
        artifacts=[],
        model_store_root="/host/models",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,expected_cls",
    [
        (ProviderType.VLLM, VLLMAdapter),
        (ProviderType.OLLAMA, OllamaAdapter),
        (ProviderType.TGI, TGIAdapter),
        (ProviderType.LLAMACPP, LlamaCppAdapter),
        (ProviderType.CLOUD, CloudAdapter),
        ("vllm", VLLMAdapter),  # string keys resolve via ProviderType(...)
    ],
)
def test_get_adapter_returns_registered_instance(key: object, expected_cls: type) -> None:
    assert isinstance(registry.get_adapter(key), expected_cls)  # type: ignore[arg-type]


def test_get_adapter_unknown_type_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry._registry, ProviderType.VLLM, None)  # type: ignore[assignment]
    del registry._registry[ProviderType.VLLM]
    with pytest.raises(ValueError, match="No adapter registered"):
        registry.get_adapter(ProviderType.VLLM)


# ──────────────────────────────────────────────────────────────────────────────
# vLLM.validate_model — pure
# ──────────────────────────────────────────────────────────────────────────────


def test_vllm_validate_no_artifacts_incompatible() -> None:
    result: CompatResult = VLLMAdapter().validate_model(_make_model(), [])  # type: ignore[arg-type]
    assert result.compatible is False
    assert "No artifacts" in result.reason


def test_vllm_validate_gguf_only_incompatible() -> None:
    adapter = VLLMAdapter()
    result = adapter.validate_model(_make_model(), [_artifact(ArtifactFormat.GGUF)])  # type: ignore[arg-type]
    assert result.compatible is False
    assert "GGUF" in result.reason
    assert "llama.cpp" in result.reason


@pytest.mark.parametrize("fmt", [ArtifactFormat.SAFETENSORS, ArtifactFormat.OTHER])
def test_vllm_validate_other_formats_compatible(fmt: ArtifactFormat) -> None:
    adapter = VLLMAdapter()
    result = adapter.validate_model(_make_model(), [_artifact(fmt)])  # type: ignore[arg-type]
    assert result.compatible is True


def test_vllm_validate_mixed_gguf_safetensors_compatible() -> None:
    adapter = VLLMAdapter()
    result = adapter.validate_model(  # type: ignore[arg-type]
        _make_model(),
        [_artifact(ArtifactFormat.GGUF), _artifact(ArtifactFormat.SAFETENSORS)],
    )
    assert result.compatible is True  # not GGUF-*only*


# ──────────────────────────────────────────────────────────────────────────────
# vLLM.build_container_spec — image selection & flag mapping (detection mocked)
# ──────────────────────────────────────────────────────────────────────────────


def test_vllm_spec_nvidia_default_uses_legacy_image_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _build(monkeypatch, _inventory(GpuVendor.NVIDIA), generic={"max_model_len": 4096})
    cmd = spec.cmd
    # enforce_eager defaults True + NVIDIA → legacy (V0) image
    assert spec.image == settings.default_vllm_legacy_image
    assert cmd[:2] == ["--model", "org/model"]
    assert "--max-model-len" in cmd and "4096" in cmd
    # legacy GPU path: float16 dtype + 1GiB default swap
    assert "--dtype" in cmd and "float16" in cmd
    assert "--swap-space" in cmd and "1" in cmd
    assert "--enforce-eager" in cmd and "--disable-frontend-multiprocessing" in cmd
    assert spec.entrypoint == ["vllm", "serve"]
    assert spec.gpu_devices == "all" and spec.gpu_vendor == GpuVendor.NVIDIA
    assert spec.ipc_mode == "host"


def test_vllm_spec_vendor_override_to_amd_uses_rocm_image(monkeypatch: pytest.MonkeyPatch) -> None:
    # detection says NVIDIA, but provider_config forces AMD
    spec = _build(
        monkeypatch,
        _inventory(GpuVendor.NVIDIA),
        generic={"enforce_eager": False, "tensor_parallel_size": 2},
        provider_cfg={"gpu_vendor": "amd"},
    )
    assert spec.image == settings.default_vllm_rocm_image
    assert spec.gpu_vendor == GpuVendor.AMD
    env = spec.env or []
    assert "HSA_OVERRIDE_GFX_VERSION=11.0.0" in env
    assert "HIP_VISIBLE_DEVICES=0,1" in env  # range(2)
    # non-legacy image + enforce_eager off → no XFORMERS backend env
    assert not any(e.startswith("VLLM_ATTENTION_BACKEND") for e in env)


def test_vllm_spec_no_gpu_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _build(monkeypatch, _inventory(GpuVendor.UNKNOWN, has_gpu=False))
    assert "--device" in spec.cmd and "cpu" in spec.cmd
    assert spec.gpu_devices is None
    assert spec.gpu_vendor is None


def test_vllm_spec_offline_env_and_hf_cache_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _build(monkeypatch, _inventory(GpuVendor.NVIDIA, has_gpu=False), generic={"enforce_eager": True})
    env = spec.env or []
    assert "HF_HUB_CACHE=/data/hf-cache" in env
    assert "HF_HUB_OFFLINE=1" in env and "TRANSFORMERS_OFFLINE=1" in env
    assert spec.volumes == ["/host/models:/data/hf-cache"]
    # no --trust-remote-code → offline mode (no HF_HUB_OFFLINE=0)
    assert "HF_HUB_OFFLINE=0" not in env


def test_vllm_spec_trust_remote_code_enables_network(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _build(
        monkeypatch,
        _inventory(GpuVendor.NVIDIA, has_gpu=False),
        provider_cfg={"engine_args": {"trust-remote-code": True}},
    )
    env = spec.env or []
    assert "HF_HUB_OFFLINE=0" in env and "TRANSFORMERS_OFFLINE=0" in env


# ── engine_args passthrough ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "engine_args,expect_present,expect_absent",
    [
        ({"enable-prefix-caching": True}, ["--enable-prefix-caching"], []),  # bool True → bare flag
        ({"enable-prefix-caching": False}, [], ["--enable-prefix-caching"]),  # bool False → omitted
        ({"max-num-seqs": 100}, ["--max-num-seqs", "100"], []),  # value → flag + value
        ({"max_model_len": 4096}, [], []),  # matches existing --max-model-len → dedup (dash vs underscore)
        ({"bad..name": "x"}, [], []),  # invalid flag name → skipped
    ],
)
def test_vllm_spec_engine_args_passthrough(
    monkeypatch: pytest.MonkeyPatch,
    engine_args: dict,
    expect_present: list,
    expect_absent: list,
) -> None:
    spec = _build(
        monkeypatch,
        _inventory(GpuVendor.NVIDIA, has_gpu=False),
        generic={"enforce_eager": False, "max_model_len": 4096},
        provider_cfg={"engine_args": engine_args},
    )
    cmd = spec.cmd
    for token in expect_present:
        assert token in cmd
    for token in expect_absent:
        assert token not in cmd


@pytest.mark.parametrize("is_legacy", [True, False])
def test_vllm_spec_legacy_only_task_flag(monkeypatch: pytest.MonkeyPatch, is_legacy: bool) -> None:
    generic = {"enforce_eager": is_legacy, "max_model_len": 4096} if not is_legacy else {"max_model_len": 4096}
    spec = _build(
        monkeypatch,
        _inventory(GpuVendor.NVIDIA, has_gpu=False),
        generic=generic,
        provider_cfg={"engine_args": {"task": "embed"}},
    )
    cmd = spec.cmd
    if is_legacy:
        assert "--task" in cmd and "embed" in cmd
    else:
        # --task removed in post-v0.7 images → must be skipped
        assert "--task" not in cmd


def test_vllm_spec_extra_args_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _build(
        monkeypatch,
        _inventory(GpuVendor.NVIDIA, has_gpu=False),
        provider_cfg={"extra_args": ["--custom-flag", "raw"]},
    )
    assert "--custom-flag" in spec.cmd and "raw" in spec.cmd


def test_vllm_default_capabilities_reflects_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vllm_adapter_module, "detect_gpus", lambda: _inventory(GpuVendor.AMD, compute_api=GpuComputeApi.ROCM))
    caps = VLLMAdapter().default_capabilities()
    assert caps["supports_gpu"] is True
    assert "safetensors" in caps["artifact_formats"]
    assert caps["gpu_vendor"] == "amd"
    assert caps["gpu_compute_api"] == "rocm"
    assert caps["gpu_count"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Stub adapters — lock their "not yet implemented" contract
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "adapter",
    [OllamaAdapter(), TGIAdapter(), LlamaCppAdapter()],
)
def test_stub_adapters_raise_not_implemented(adapter: object) -> None:
    with pytest.raises(NotImplementedError):
        adapter.validate_model(_make_model(), [])  # type: ignore[union-attr]
    with pytest.raises(NotImplementedError):
        adapter.build_container_spec(  # type: ignore[union-attr]
            _make_runtime(), _make_provider(), _make_model(), [], "/host/models"
        )


def test_stub_adapters_expose_static_capabilities() -> None:
    assert "gguf" in OllamaAdapter().default_capabilities()["artifact_formats"]
    assert TGIAdapter().default_capabilities()["supports_embeddings"] is False
    assert LlamaCppAdapter().default_capabilities()["artifact_formats"] == ["gguf"]


def test_cloud_adapter_validate_always_compatible() -> None:
    assert CloudAdapter().validate_model(_make_model(), []).compatible is True  # type: ignore[arg-type]


def test_cloud_adapter_build_spec_raises() -> None:
    with pytest.raises(NotImplementedError):
        CloudAdapter().build_container_spec(  # type: ignore[arg-type]
            _make_runtime(), _make_provider(), _make_model(), [], "/host/models"
        )
