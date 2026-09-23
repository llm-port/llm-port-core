"""Inspect and report versions of all critical components in the runtime."""

import json
import sys
from typing import Any, Dict


def get_runtime_versions() -> Dict[str, Any]:
    """Collect installed versions of Ray, vLLM, Torch, CUDA, and dependencies."""
    report: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "ray": None,
        "vllm": None,
        "torch": None,
        "cuda": None,
        "transformers": None,
        "pyarrow": None,
        "triton": None,
        "flash_attn": None,
        "flashinfer": None,
        "nccl": None,
    }

    try:
        import ray
        report["ray"] = ray.__version__
    except ImportError:
        pass

    try:
        import vllm
        report["vllm"] = vllm.__version__
    except ImportError:
        pass

    try:
        import torch
        report["torch"] = torch.__version__
        report["cuda"] = torch.version.cuda
        # Not gated on is_available(): reading NCCL's version needs the
        # library, not a device. The gate meant every manifest written at
        # build time -- where there is never a GPU -- recorded nccl as None,
        # so the catalogue lost a version it used to show.
        try:
            nccl = torch.cuda.nccl.version()
            report["nccl"] = (
                ".".join(str(part) for part in nccl)
                if isinstance(nccl, tuple)
                else str(nccl)
            )
        except Exception:
            pass
    except ImportError:
        pass

    try:
        import transformers
        report["transformers"] = transformers.__version__
    except ImportError:
        pass

    try:
        import pyarrow
        report["pyarrow"] = pyarrow.__version__
    except ImportError:
        pass

    try:
        import triton
        report["triton"] = triton.__version__
    except ImportError:
        pass

    try:
        import flash_attn
        report["flash_attn"] = getattr(flash_attn, "__version__", "installed")
    except ImportError:
        pass

    try:
        import flashinfer
        report["flashinfer"] = getattr(flashinfer, "__version__", "installed")
    except ImportError:
        pass

    return report


def print_versions_json() -> None:
    """Print the runtime versions formatted as JSON."""
    data = get_runtime_versions()
    print(json.dumps(data, indent=2))

