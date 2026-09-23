# LLM.Port Ray runtime image

The container LLM.Port runs models in. Ray 2.58 + Ray Serve LLM + vLLM, built
as one thin layer on top of NVIDIA's own vLLM image.

This is a release artifact, not a test fixture. The backend reads
`runtime-manifest.json` from this directory at run time to build its bundle
catalogue, so nothing here is optional: without it the catalogue cannot be
constructed and no deployment can resolve a runtime.

It used to live in `llm-port-dev/llm_port_ray_migration/runtime_image/`, which
meant a core checkout had to have the dev repo beside it to start. The
certification suite -- the scripts that prove a built image works on real DGX
hardware, and the reports they produced -- stayed there, because that is what
it is.

## What is here

| | |
|---|---|
| `Dockerfile` | aarch64 / GB10 (DGX Spark, sm_121) |
| `Dockerfile.x86_64` | generic x86_64 NVIDIA, Turing (sm_75) through Blackwell (sm_120) |
| `llm_port_ray_runtime/` | the helper package baked into the image; provides the `llm-port-ray-runtime` CLI the agent drives |
| `setup.py` | packaging for that helper |
| `fixtures.json`, `test_local_validation.py` | copied into the image; validate imports, dependencies and `LLMConfig` shapes from inside it |
| `runtime-manifest.json`, `runtime-manifest-x86_64.json` | the minted identity of each built image |
| `rebuild_runtime_image.py` | builds an image and emits its manifest |
| `mint_x86_manifest.py` | mints the x86_64 manifest from a built image |

## The manifests are minted, not written

Every field in a manifest -- the image reference, its id, its layer digests,
the stack versions, the architectures it was compiled for -- is read out of
the built image. None of it is typed by hand. That is the whole point: a
catalogue entry that disagrees with the artifact it names is a pin that pins
nothing, and it fails at deploy time on a node, which is the worst place to
find out.

So a manifest is only ever updated by rebuilding:

```bash
python rebuild_runtime_image.py            # aarch64 / GB10
python mint_x86_manifest.py                # x86_64, from an already-built image
```

Then certify the result against real hardware before the catalogue points at
it -- the suite for that is in
`llm-port-dev/llm_port_ray_migration/runtime_image/`.
