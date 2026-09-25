# Releasing LLM.Port

A release is a tag. The scripts in `llm-port-dev/scripts/` bump what needs
bumping, tag, and push; GitHub Actions does all the building. Nothing is built
or uploaded from a developer's machine.

| Script | Tag | Workflow | Produces |
|---|---|---|---|
| `release-core.ps1 -Version 0.3.0` | `core-v0.3.0` | `docker-publish.yml` | `ghcr.io/llm-port/{base,api,backend,frontend,pii,mcp,skills}:0.3.0` (and `0.3`, `latest`) |
| `release-cli.ps1 -Version 0.3.0` | `cli-v0.3.0` | `cli-release.yml` | `llmport-cli` on PyPI, standalone CLI binaries, a GitHub release |
| `release-node-agent.ps1 -Version 0.1.14` | `node-agent-v0.1.14` | `node-agent-release.yml` | `llmport-agent-linux-x86_64` and `-linux-aarch64` as GitHub release assets |
| `release-runtime.ps1 -Version 2.58.0-1` | `runtime-v2.58.0-1` | `runtime-image-release.yml` | `ghcr.io/llm-port/ray-runtime-{gb10,x86_64}:2.58.0-1`, manifests on a pre-release |

Every script refuses a dirty tree and an existing tag, and takes `-DryRun`.

A plain `v0.3.0` tag starts `docker-publish.yml` and `cli-release.yml`
together; the CLI job waits until the images of its version exist, so nobody can
install a CLI whose images are missing.

## What each one checks

- **Core images.** The backend image carries the runtime manifests from
  `llm_port_runtime_image/` (a named build context), because its catalogue of
  runtime images is built from them. Pushes to `master` rebuild only the
  services that changed.
- **Node agent.** The agent's `pyproject.toml`, its `__version__` and the
  backend's `AGENT_VERSION` must agree -- the backend is what tells machines
  which agent to install -- and `release-node-agent.ps1` bumps all three. Each
  binary is built in Debian bookworm on a runner of its own architecture and
  smoke-tested there, so it runs on glibc 2.36 and newer.
- **Runtime images.** Each image is built on a runner of its own
  architecture, validated inside itself (imports, `pip check`, Ray's
  `LLMConfig` against LLM.Port's deployment shapes) before anything is pushed,
  then pushed, and its manifest is minted from the pushed image by
  `llm_port_runtime_image/rebuild_runtime_image.py` -- the script developers use.

## Runtime images need certifying before they ship

CI has no GB10 pair and no NVIDIA card, so the manifests it attaches to the
`runtime-v…` pre-release say **uncertified**. Before the backend points at a new
runtime image:

1. Certify it on its hardware (the suite is in
   `llm-port-dev/llm_port_ray_migration/runtime_image/`).
2. Commit its manifest to `llm_port_runtime_image/`
   (`runtime-manifest.json` for GB10, `runtime-manifest-x86_64.json` for x86_64).
3. Release core; the new backend image carries the manifest.

Until step 3 the released backend keeps the build it was certified with.

## A full release, in order

1. `release-runtime.ps1` -- only when the runtime images changed; then certify
   and commit the manifests.
2. `release-node-agent.ps1` -- only when the agent changed. Its version is
   baked into the backend, so this comes before core.
3. `release-core.ps1`.
4. `release-cli.ps1` with the same version as core: `llmport deploy` pulls the
   images tagged with the CLI's own version.

## Repository settings the workflows use

| Name | Kind | Needed for |
|---|---|---|
| `PYPI_API_TOKEN` | secret | the CLI on PyPI; the agent's PyPI upload is skipped without it |
| `NGC_API_KEY` | secret, optional | only if NVIDIA's base image for the GB10 runtime stops being public |
| `RUNTIME_RUNNER_GB10`, `RUNTIME_RUNNER_X86_64` | variables, optional | larger runners, if a runtime image build runs out of disk on the standard ones |

Images go to GHCR with the workflow's own token. A new package on GHCR starts
private: make `ray-runtime-gb10` and `ray-runtime-x86_64` public (or give the
servers a pull credential) after their first release.
