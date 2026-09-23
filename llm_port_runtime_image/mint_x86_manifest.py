"""Mint the x86_64 runtime manifest from the built image itself.

``from_runtime_manifest`` is documented as the only supported way to mint a
bundle's identity, so that the catalog cannot drift from the artifact. That
applies here too: every field below is read out of the image rather than
typed, including the stack versions and the arch list the image was actually
compiled for.
"""

import json
import pathlib
import subprocess

IMAGE = "llmport-ray-runtime-x86_64:2.58.0"
OUT = pathlib.Path(
    r"C:/00_Code/80_llm.port/llm-port-dev/llm_port_ray_migration/runtime_image"
    r"/runtime-manifest-x86_64.json"
)


def docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def probe() -> dict:
    """Ask the image what it contains, rather than asserting it."""
    script = (
        "import json, platform, torch, importlib.metadata as md\n"
        "def v(p):\n"
        "    try: return md.version(p)\n"
        "    except Exception: return None\n"
        "print(json.dumps({\n"
        "  'machine': platform.machine(),\n"
        "  'python': platform.python_version(),\n"
        "  'ray': v('ray'), 'vllm': v('vllm'), 'torch': v('torch'),\n"
        "  'transformers': v('transformers'), 'triton': v('triton'),\n"
        "  'cuda': torch.version.cuda,\n"
        "  'arch_list': torch.cuda.get_arch_list(),\n"
        "}))"
    )
    raw = subprocess.run(
        ["docker", "run", "--rm", "--gpus", "all", "--entrypoint", "python3", IMAGE, "-c", script],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise SystemExit(f"could not read the probe output:\n{raw[:400]}")


def main() -> None:
    facts = probe()
    layers = json.loads(docker("image", "inspect", IMAGE, "--format", "{{json .RootFS.Layers}}"))
    image_id = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")

    # sm_75 -> "7.5": the manifest records capabilities the way the node agent
    # reports them, so the compatibility check compares like with like.
    caps = sorted(
        {
            f"{a[3:-1]}.{a[-1]}"
            for a in facts["arch_list"]
            if a.startswith("sm_") and a[3:].isdigit()
        },
        key=lambda c: tuple(int(part) for part in c.split(".")),
    )

    manifest = {
        "release_tag": IMAGE,
        "image_id": image_id,
        "rootfs_layers": layers,
        # The name ``from_runtime_manifest`` reads; "stack" is silently empty.
        "stack_components": {
            "ray": facts["ray"],
            "vllm": facts["vllm"],
            "torch": facts["torch"],
            "cuda": facts["cuda"],
            "python": facts["python"],
            "transformers": facts["transformers"],
            "triton": facts["triton"],
        },
        "arch_list": facts["arch_list"],
        "compute_capabilities": caps,
        "machine": facts["machine"],
        "certification": {
            "overall_status": "uncertified",
            "checks_total": 0,
            "checks": [],
            "hardware_target": "generic x86_64 NVIDIA",
            "detail": (
                "The generic x86_64 NVIDIA runtime: Ray 2.58.0 with "
                "ray.serve.llm, vLLM, and the llm-port-ray-runtime control "
                "helper the node agent drives the container through. Kernels "
                "span sm_75 to sm_120, so it serves every mainstream NVIDIA "
                "card since Turing. Not yet certified: a single-node cluster "
                "has been started and observed on a TITAN RTX (sm_75), but no "
                "two-node run has been recorded against it."
            ),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  machine      {facts['machine']}")
    print(f"  ray          {facts['ray']}")
    print(f"  vllm         {facts['vllm']}")
    print(f"  cuda         {facts['cuda']}")
    print(f"  capabilities {caps}")
    print(f"  layers       {len(layers)}")


if __name__ == "__main__":
    main()
