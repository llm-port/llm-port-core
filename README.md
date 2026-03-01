# llm.port CLI

The single entry point to install, configure, and manage the **llm.port** platform.

## Developer Setup

### Prerequisites

- **Python 3.12+**
- **uv** — Install from [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Docker** + **Docker Compose v2**
- **Git**

### Install from source (development)

```bash
# Clone the repo
git clone https://github.com/llm-port/llm-port-cli.git
cd llm-port-cli

# Install dependencies + entry point in editable mode
uv sync

# Run commands via uv
uv run llmport --help
uv run llmport version
uv run llmport doctor
```

### Install globally (from local source)

```bash
cd llm-port-cli

# Option A: install as a uv tool from local path
uv tool install --editable .

# Option B: install via pip into an existing environment
pip install -e .

# Now use directly from anywhere
llmport --help
```

> **Note:** `uv tool install llmport-cli` (from PyPI) is not yet available.
> Use the local install methods above during development.

### Verify installation

```bash
llmport version       # Print CLI + runtime versions
llmport doctor        # Check system requirements
```

## Usage

### Bootstrap a new development workspace

```bash
# Clone all repos + install deps + start infra + run migrations
llmport dev init C:\Projects\llm-port

# With SSH cloning instead of HTTPS
llmport dev init C:\Projects\llm-port --ssh

# Force pull latest on existing repos
llmport dev init C:\Projects\llm-port --overwrite

# Specify a branch
llmport dev init C:\Projects\llm-port --branch develop
```

### Start the dev stack

```bash
# Start shared infrastructure + backend + frontend in separate terminals
llmport dev up

# From a specific workspace directory
llmport dev up --workspace C:\Projects\llm-port
```

### Stop the dev stack

```bash
llmport dev down
```

### Check status

```bash
# Show running containers + dev processes
llmport dev status

# Show container status (production)
llmport status
```

### Production deployment

```bash
# Interactive setup wizard
llmport init

# Non-interactive
llmport init \
  --install-dir /opt/llmport \
  --admin-email admin@company.com \
  --admin-password s3cret \
  --modules rag,pii \
  --gpu auto

# Start / stop
llmport up
llmport down
```

### Module management

```bash
llmport module list
llmport module enable rag
llmport module disable pii
```

### Configuration

```bash
llmport config show          # Print current config (secrets masked)
llmport config set KEY VALUE # Update a config value
llmport config edit          # Open config in $EDITOR
```

### Logs

```bash
llmport logs                 # Tail all service logs
llmport logs --service backend
```

### System diagnostics

```bash
llmport doctor
```

Output:
```
┌─ System Check ──────────────────────────────────────┐
│ OS            Windows 11 (10.0.26100)         ✓     │
│ Docker        27.3.0                          ✓     │
│ Compose       v2.30.0                         ✓     │
│ GPU           AMD Radeon 780M · 8 GB · ROCm   ✓     │
│ RAM           32 GB available                 ✓     │
│ Disk          214 GB free                     ✓     │
│ Ports         All 16 ports available          ✓     │
└─────────────────────────────────────────────────────┘
```

## Command Reference

| Command | Description |
|---|---|
| `llmport version` | Print CLI and runtime versions |
| `llmport doctor` | Run system health checks |
| `llmport init` | Production setup wizard |
| `llmport up` | Start all services |
| `llmport down` | Stop all services |
| `llmport status` | Show service status |
| `llmport logs` | Stream service logs |
| `llmport config show\|set\|edit` | Manage configuration |
| `llmport module list\|enable\|disable` | Toggle platform modules |
| `llmport dev init [path]` | Bootstrap dev workspace |
| `llmport dev up` | Start dev stack |
| `llmport dev down` | Stop dev stack |
| `llmport dev status` | Show dev environment status |

## Project Structure

```
llm_port_cli/
├── pyproject.toml              # uv-managed, entry point: llmport
├── src/
│   └── llmport/
│       ├── cli.py              # Click root group
│       ├── commands/           # Command implementations
│       │   ├── doctor.py
│       │   ├── init_cmd.py
│       │   ├── up.py
│       │   ├── status.py
│       │   ├── logs.py
│       │   ├── module.py
│       │   ├── config.py
│       │   ├── version.py
│       │   └── dev/            # Dev-mode commands
│       │       ├── dev_init.py
│       │       ├── dev_up.py
│       │       └── dev_status.py
│       ├── core/               # Shared utilities
│       │   ├── compose.py      # Docker Compose wrapper
│       │   ├── detect.py       # System detection
│       │   ├── settings.py     # Config management
│       │   ├── api_client.py   # Backend API client
│       │   ├── env_gen.py      # .env generation
│       │   ├── git.py          # Git helpers
│       │   └── console.py      # Rich console
│       ├── tui/                # Textual TUI (init wizard)
│       └── templates/          # Jinja2 templates
└── tests/
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
