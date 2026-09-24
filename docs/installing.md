# Installing LLM.Port

LLM.Port runs as a set of containers on one Linux server. Its command-line
tool, `llmport`, installs it, upgrades it and backs it up. The machines with
GPUs join afterwards, each through a one-line install from the console.

---

## What you need

- **A Linux server, x86_64.** The published images are x86_64 only. The server
  needs no GPU; the models run on the machines you add. The test install uses
  8 cores, 7 GB of memory and 100 GB of disk.
- **Docker Engine 24 or later with Compose v2**, and your user in the
  `docker` group.
- **Python 3.12 or later**, with `pipx` or `uv` to install the CLI.
- **Port 80 free.** The console, the API and the OpenAI-compatible gateway
  are all served on it. To use another port, see [Changing the port](#changing-the-port).

## Install

```bash
pipx install llmport-cli        # or: uv tool install llmport-cli
llmport deploy
```

`llmport deploy`:

1. checks Docker;
2. writes the deployment files into `~/llm-port` (or the directory you pass:
   `llmport deploy /opt/llm-port`);
3. generates `.env` there, with a random password for every service;
4. pulls the images for the CLI's own version, for example
   `ghcr.io/llm-port/backend:0.3.0`;
5. starts everything and creates the first admin. It asks for the admin's
   email and a password (leave the password blank to have one generated),
   then shows both.

With `llmport deploy -y` nothing is asked. The admin is `admin@localhost`
with a generated password, and both are saved in
`~/llm-port/.bootstrap-credentials`. Store them somewhere safe, then delete
that file.

Open `http://<server>` in a browser and sign in.

### Where things are

| | |
|---|---|
| `~/llm-port/` | the deployment: compose file, `.env`, service configuration |
| `~/llm-port/.env` | every password and setting; keep it private |
| `~/llm-port/backups/` | backups (see below) |
| `~/.config/llmport/llmport.yaml` | the CLI's own settings: where the install is, which modules are on |

The data lives in Docker volumes named `llm_port_shared_*` (for example
`llm_port_shared_pg_data`). `llmport down` keeps them; only
`llmport down --volumes` removes them.

## Add the machines that run the models

In the console, open **Machines → Add a machine** and run the line it shows
on each GPU machine. The machine asks to join, and you approve it on the
same page. See [Onboarding a node](onboarding-a-node.md).

The line installs the node agent from the server itself, or from the
project's GitHub releases when the server has no copy. For machines without
internet access, put the agent builds in `~/llm-port/agent-binaries/`
(`llmport-agent-linux-x86_64`, `llmport-agent-linux-aarch64`, from the
[releases page](https://github.com/llm-port/llm-port-core/releases)).

## Upgrade

```bash
pipx upgrade llmport-cli        # or: uv tool upgrade llmport-cli
llmport upgrade
```

The upgrade backs up every database, replaces the deployment files with the
new release's, pulls the new images and restarts the services. Your `.env`
is kept as it is; a release only adds settings it introduces. Changes you
made to the deployment files themselves (`nginx/nginx.conf`, for example)
are replaced, so keep your changes in `.env`.

An upgrade only moves forward: the databases are migrated to the new
release. To go back, restore the backup the upgrade made. See
[Upgrading LLM.Port](upgrading.md).

## Back up and restore

```bash
llmport backup                          # into ~/llm-port/backups/<time>/
llmport restore ~/llm-port/backups/<time>
```

A backup holds a dump of every database, the `.env`, and the migration
versions. Keep copies off the server: a backup on the same disk doesn't
survive losing the disk.

`llmport deploy` offers a nightly backup (at 03:00, keeping the last 7), and
turns it on with `-y`. To change or stop it:

```bash
llmport backup schedule --at 01:30 --retain 14
llmport backup schedule --off
```

It runs as a systemd user timer (`systemctl --user list-timers`), or from
your crontab where there is no systemd. The output goes to
`~/llm-port/backups/backup.log`.

If the server itself is lost, install a new one, restore the backup onto it,
and re-run the install line on each machine. Clusters that were created
after the backup keep running on the machines, and the new server can take
them over without restarting any model. See
[Taking over clusters your machines still run](taking-over-clusters.md).

## Changing the port

Set `LLM_PORT_HTTP_PORT` in `~/llm-port/.env` and apply it:

```bash
llmport up
```

## Installing from source

To build the images yourself, for example to run changes of your own, deploy
from a checkout instead:

```bash
git clone https://github.com/llm-port/llm-port-core.git
cd llm-port-core
pipx install ./llm_port_cli
llmport deploy --build
```

An install from a checkout builds its images on every `llmport upgrade`.
Pull new code first (`git pull`).
