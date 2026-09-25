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
| `rebuild_runtime_image.py` | builds either image (`--flavor gb10` or `x86_64`), optionally pushes it, and emits its manifest |

## The manifests are minted, not written

Every field in a manifest -- the image reference, its id, its layer digests,
the stack versions, the architectures it was compiled for -- is read out of
the built image. None of it is typed by hand. That is the whole point: a
catalogue entry that disagrees with the artifact it names is a pin that pins
nothing, and it fails at deploy time on a node, which is the worst place to
find out.

So a manifest is only ever updated by rebuilding:

```bash
python rebuild_runtime_image.py --build                   # aarch64 / GB10, on a DGX node
python rebuild_runtime_image.py --flavor x86_64 --build   # x86_64, on any x86_64 machine
```

Then certify the result against real hardware before the catalogue points at
it -- the suite for that is in
`llm-port-dev/llm_port_ray_migration/runtime_image/`.

## Releases

A `runtime-v<version>` tag (pushed by `llm-port-dev/scripts/release-runtime.ps1`)
runs `.github/workflows/runtime-image-release.yml`: each image is built on a
runner of its own architecture, pushed to
`ghcr.io/llm-port/ray-runtime-gb10:<version>` and
`ghcr.io/llm-port/ray-runtime-x86_64:<version>`, and its manifest -- minted by
the same script, with the registry digest -- is attached to the
`runtime-v<version>` GitHub release.

Those manifests say `uncertified`, because CI has no GB10 pair or NVIDIA card.
Certify each image on its hardware, then commit its manifest here; the next
core release builds the backend with it, and from then on the catalogue points
at the published image.
