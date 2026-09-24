# llmport-cli

[![PyPI version](https://img.shields.io/pypi/v/llmport-cli)](https://pypi.org/project/llmport-cli/)
[![Python 3.12+](https://img.shields.io/pypi/pyversions/llmport-cli)](https://pypi.org/project/llmport-cli/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/llm-port/llm-port-cli/blob/main/LICENSE)

The single entry point to **deploy, configure, and manage** the
[llm.port](https://llm-port.github.io) platform — an open-source LLM gateway
that routes, secures, and observes traffic across local runtimes and remote
providers.

```
pipx install llmport-cli       # or: uv tool install llmport-cli
llmport deploy                 # installs LLM.Port into ~/llm-port and starts it
```

The CLI carries the deployment files of its release and runs that release's
published images: `llmport-cli 0.3.0` deploys LLM.Port 0.3.0. To upgrade,
upgrade the CLI and run `llmport upgrade`. See
[Installing LLM.Port](https://github.com/llm-port/llm-port-core/blob/master/docs/installing.md).

---

## Features

- **One-command production deploy** — pre-flight checks, `.env` generation,
  the release's images, database migrations, and service startup.
- **Upgrade, backup and restore** — `llmport upgrade` backs up every database,
  moves the install to the CLI's release and keeps your settings.
- **Auto-tuning** — detects host CPU / RAM and computes optimal worker counts,
  DB pool sizes, and queue channel pools.
- **Module management** — enable / disable optional services
  (PII redaction, Auth, Mailer, Docling OCR) via Docker Compose profiles.
- **GPU auto-detection** — discovers NVIDIA (CUDA), AMD (ROCm), and Intel GPUs;
  selects the correct vLLM container image automatically.
- **Developer workflow** — clone all repos, install deps, run infra + migrations,
  and generate a VS Code workspace in one command.
- **Rich terminal UI** — tables, progress bars, and themed output powered by
  [Rich](https://github.com/Textualize/rich). Interactive TUI wizard via
  [Textual](https://github.com/Textualize/textual).
- **Command abbreviation** — type `llmport st` instead of `llmport status`.

---

## Requirements

| Dependency     | Minimum |
| -------------- | ------- |
| Python         | 3.12    |
| Docker Engine  | 24.0    |
| Docker Compose | v2      |

Git is only needed to deploy from a source checkout (`--build`) or for the
developer workflow.

---

## Installation

### From PyPI

```bash
pip install llmport-cli

# or with uv
uv tool install llmport-cli
```

### From source

```bash
git clone https://github.com/llm-port/llm-port-core.git
cd llm-port-core/llm_port_cli
uv sync                   # install deps + editable entry point
uv run llmport --help
```

### Verify

```bash
llmport version
llmport doctor
```

---

## Quick start

### Production deployment

```bash
# Full deploy — pre-flight, env gen, pull this release's images, migrate, start
llmport deploy                      # into ~/llm-port
llmport deploy /opt/llmport         # into a directory of your choice

# Enable optional modules
llmport deploy --modules pii,auth

# From a source checkout: build the images instead of pulling them
llmport deploy --build

# Upgrade to the CLI's release (backs up first), back up, restore
llmport upgrade
llmport backup
llmport restore ~/llm-port/backups/<time>

# Provision node-agent on this host during deploy
llmport deploy /opt/llmport --local-node

# Provision node-agent on remote host over SSH
llmport deploy /opt/llmport --local-node --local-node-host ubuntu@10.0.0.12
```

### Day-to-day operations

```bash
llmport up                          # start all services
llmport up --build llm-port-api     # rebuild a single service
llmport down                        # stop all services
llmport down --volumes              # stop + remove volumes
llmport status                      # service table
llmport logs -f                     # tail all logs
llmport logs -f llm-port-backend    # tail one service
```

### Modules

```bash
llmport module list                 # show available modules
llmport module enable pii skills    # enable modules
llmport module disable mcp          # disable a module
```

### Configuration

```bash
llmport config show                 # print current config
llmport config set dev.branch main  # update a value (dot-notation)
llmport config edit                 # open in $EDITOR
llmport config path                 # print config file location
```

### Auto-tuning

```bash
llmport tune                        # detect resources, write .env
llmport tune --profile prod         # production-grade sizing
llmport tune --dry-run              # preview without writing
```

### Admin utilities

```bash
llmport admin reset-password --email admin@localhost
```

### Developer workflow

```bash
# Bootstrap everything — clone repos, install deps, start infra,
# run migrations, generate VS Code workspace
llmport dev init ~/projects/llm-port
llmport dev init ~/projects/llm-port --ssh        # SSH cloning
llmport dev init ~/projects/llm-port --overwrite   # force pull

# Start / stop / status
llmport dev up
llmport dev up --backend-only
llmport dev up --modules pii,mcp,skills   # also run the optional modules
llmport dev up --local-node
llmport dev up --local-node --local-node-host ubuntu@10.0.0.12
llmport dev down
llmport dev status

# Check dev prerequisites (optionally auto-install missing tools)
llmport dev doctor
llmport dev doctor --install --yes
```

`--modules` runs PII (8003), MCP (8007) and skills (8008) on the host beside
the backend, and points the gateway and backend at them. Without it, the
modules switched on with `llmport module enable` run; the others are switched
off in the gateway and backend. See
[What the gateway does with a chat request](../docs/gateway-pipeline.md).

`--local-node` installs a systemd unit by default and requires sudo on Linux.
Use `--local-node-no-sudo` to skip privileged systemd setup.

---

## Command reference

| Command                                      | Description                                                   |
| -------------------------------------------- | ------------------------------------------------------------- |
| `llmport version`                            | Print CLI, Python, Docker, and Compose versions               |
| `llmport doctor`                             | Run system health checks (OS, RAM, disk, Docker, GPU, ports)  |
| `llmport deploy [DIR]`                       | Full production deployment with pre-flight checks             |
| `llmport upgrade`                            | Back up, move to the CLI's release, restart with health gate  |
| `llmport backup` / `llmport restore DIR`     | Dump every database and `.env` / put them back                |
| `llmport deploy [DIR] --local-node`          | Deploy + provision node-agent (local or SSH host)            |
| `llmport up [SERVICES...]`                   | Start services (supports `--build`, `--pull`)                 |
| `llmport down`                               | Stop and remove containers (`--volumes`, `--all`)             |
| `llmport status`                             | Show service state, health, and ports (`--json`)              |
| `llmport logs [SERVICES...]`                 | Stream logs (`-f`, `-n`, `--timestamps`)                      |
| `llmport config show\|set\|edit\|path\|init` | Manage YAML configuration                                     |
| `llmport module list\|enable\|disable`       | Toggle optional platform modules                              |
| `llmport tune`                               | Auto-tune worker and pool settings (`--profile`, `--dry-run`) |
| `llmport admin reset-password`               | Reset a user password directly in the database                |
| `llmport dev init [DIR]`                     | Bootstrap full developer workspace                            |
| `llmport dev up`                             | Start backend, worker, and frontend dev servers               |
| `llmport dev up --modules pii,mcp,skills`    | Also run the optional modules on the host                     |
| `llmport dev up --local-node`                | Start dev services and provision node-agent                   |
| `llmport dev down`                           | Stop all dev processes                                        |
| `llmport dev status`                         | Show repo branches, infra, and dev processes                  |
| `llmport dev doctor`                         | Check dev prerequisites (`--install`)                         |

---

## Project structure

```
llm_port_cli/
├── pyproject.toml
├── src/
│   └── llmport/
│       ├── cli.py                 # Click root group + AliasedGroup
│       ├── commands/
│       │   ├── version.py         # version
│       │   ├── doctor.py          # doctor
│       │   ├── deploy.py          # deploy
│       │   ├── up.py              # up
│       │   ├── down.py            # down
│       │   ├── status.py          # status
│       │   ├── logs_cmd.py        # logs
│       │   ├── config.py          # config group
│       │   ├── module.py          # module group
│       │   ├── tune.py            # tune
│       │   ├── admin.py           # admin group
│       │   └── dev/               # developer workflow
│       │       ├── dev_init.py
│       │       ├── dev_up.py
│       │       ├── dev_status.py
│       │       └── dev_doctor.py
│       ├── core/                  # shared utilities
│       │   ├── compose.py         # Docker Compose wrapper
│       │   ├── detect.py          # OS / GPU / port detection
│       │   ├── settings.py        # YAML config (~/.config/llmport/)
│       │   ├── api_client.py      # Backend REST client (httpx)
│       │   ├── bootstrap.py       # First-admin bootstrap
│       │   ├── env_gen.py         # .env generation (Jinja2)
│       │   ├── git.py             # Git clone / checkout helpers
│       │   ├── install.py         # Cross-platform tool installer
│       │   ├── registry.py        # Central metadata registry
│       │   ├── sysinfo.py         # Resource detection + tuning
│       │   └── console.py         # Rich themed output
│       ├── tui/                   # Textual TUI wizard
│       └── templates/             # Jinja2 .env templates
└── tests/
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

Part of the [llm.port](https://github.com/llm-port) project.
