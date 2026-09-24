# Upgrading LLM.Port

How to move an installation to a newer version, keeping its data. Written
from upgrading a real installation -- a 13 September build on an Ubuntu VM,
to the `ray` branch -- and every problem that upgrade hit is in
[If the upgrade stops](#if-the-upgrade-stops).

## What `llmport upgrade` does

It upgrades the installation the CLI is configured for
(`install_dir` in `~/.config/llmport/llmport.yaml`), in this order:

1. **Checks** Docker, and that no container it is about to replace was
   created by something else (see below).
2. **Backs up** every database on the Postgres server and the `.env` file,
   to `<install_dir>/backups/<time>/`. It stops if nothing could be backed up.
3. **Refreshes the deployment files and `.env`.** An install made from the
   published images gets the new release's deployment files. `.env` keeps
   every value it has; a release only adds the settings it introduces. Then
   RabbitMQ's users are written from it (`rabbitmq/definitions.json`).
4. **Gets the new images.** An install made from the published images pulls
   the images of the CLI's version. An install from a source checkout builds
   them from the checkout (`--no-build` skips this).
5. **Restarts** everything: infrastructure, then the migrations, then the
   application and modules. Database migrations run here.
6. **Clears ClickHouse's old diagnostic logs** (see
   [The disk is full](#the-disk-is-full)).
7. **Waits for the backend** to report healthy.

The new version comes from the CLI (published images) or from the
checkout (source), so put that in place first.

## Before you start

**Free disk space.** The new images need a few GB (building them from
source, about 10 GB), and the backup needs room for your databases. Check with `df -h /`.

If the disk is already full -- Postgres, ClickHouse, Loki and Grafana
restarting over and over is the sign -- see [The disk is full](#the-disk-is-full)
first. Nothing else will work until there is space.

**Know where the installation is.**

```bash
llmport config show        # install_dir is the llm_port_shared directory
```

## Installed with `pipx install llmport-cli`

```bash
pipx upgrade llmport-cli        # or: uv tool upgrade llmport-cli
llmport upgrade --dry-run       # shows the release it moves to
llmport upgrade -y
```

Then [check it](#check-it). The CLI refuses to move an install to an
older release than the one it runs: the databases were migrated forward.
Going back is a [restore](#going-back).

## Installed from a source checkout

### 1. Put the new version in place

For an installation made with `llmport dev init` or `git clone`:

```bash
cd ~/llm-port-core
git stash push -m "local edits before upgrade"   # only if `git status` shows changes
git fetch
git checkout <release tag or branch>
```

Local edits are kept in the stash (`git stash list`); re-apply them with
`git stash pop` after the upgrade if you still need them.

### 2. Update the CLI

The upgrade logic is in the CLI, so update it before running it:

```bash
uv tool install --force ~/llm-port-core/llm_port_cli
llmport --version
```

### 3. Upgrade

```bash
llmport upgrade --dry-run   # what it would do
llmport upgrade -y
```

On an 8-core VM the image builds took about 15 minutes and the rest a few
minutes.

## Check it

```bash
llmport status                          # every service running; healthy where it has a check
curl -s -o /dev/null -w "%{http_code}\n" http://localhost/api/health   # 200
```

Then sign in to the console. What the upgraded VM showed:

- All 18 services up (the two migrators exit once they have run).
- The database migrations ran from the 13 September versions to the
  current ones (backend `us3rpr3f0001` to `h1br1dsrch`, gateway
  `5j6k7l8m9n0p` to `6k7l8m9n0p1q`).
- Every row kept: users, providers, models, runtimes, the enrolled
  machine, the gateway's routes. The only additions were the permissions
  new features need (99 to 114).
- ClickHouse's old diagnostic logs dropped; the disk at 51%.

## If the upgrade stops

### Settings missing after an earlier upgrade

Before this release, `llmport upgrade` rewrote `.env` from the defaults and
kept only the passwords. Everything else was lost, including `HF_CACHE_DIR`
(the host's model cache, set by `llmport deploy`), the admin name synced to
Grafana, and any port you had changed. Upgrades now keep every value.

To get the lost values back, compare `.env` with the copy the upgrade saved
in its backup, and copy the missing lines back:

```bash
diff <(grep -o '^[A-Z_]*=' .env.bak) <(grep -o '^[A-Z_]*=' .env)   # in backups/<time>/ and the install dir
```

Then run `llmport up` to apply them.

### "These containers have names this installation uses, but it did not create them"

A container with one of LLM.Port's names (`llm-port-prometheus`, ...) was
created some other way -- by hand with `docker run`, or by another compose
project. Compose cannot replace it, and before this check the upgrade
stopped on it only after building every image. The message lists the
containers and the command to remove them:

```bash
docker rm -f llm-port-prometheus
llmport upgrade -y --no-build     # the images are already built
```

Removing a container keeps its data volumes.

### "Bind for 127.0.0.1:9090 failed: port is already allocated"

Before this release Prometheus and MinIO both took host port 9090, and
only a hand-edited `.env` kept them apart. Prometheus is now on
`127.0.0.1:9099` on the host (still `prometheus:9090` inside the stack).
If you had set `PROM_PORT` yourself, that still wins.

### The gateway or backend never becomes healthy: "RabbitMQ not ready", "invalid credentials"

RabbitMQ creates its users from `rabbitmq/definitions.json` every time it
starts, and the services log in with the passwords in `.env`. Before this
release only `llmport deploy` wrote that file, so an installation upgraded
with an older CLI kept a file with other passwords: RabbitMQ was healthy,
and refused the gateway and the backend. The upgrade now writes the file
from `.env`. Update the CLI first (step 2) and run the upgrade again.

To check by hand (the password is `RABBITMQ_API_PASS` in `.env`):

```bash
docker exec llm-port-rmq rabbitmqctl authenticate_user llmport-api '<password>'
```

### The disk is full

Up to this release ClickHouse (which stores Langfuse's traces) kept its own
diagnostic logs forever. On the upgraded VM `system.trace_log` alone held
41 GB of a 98 GB disk; Langfuse's actual data was a few kilobytes. On a
busy workstation it grew about 3 GB a day.

This release turns those logs off
(`llm_port_shared/clickhouse/config.d/system-logs.xml`) and the upgrade
drops what they hold. But an installation whose disk is already full cannot
get that far. Free space by hand first:

1. Remove ClickHouse images no container uses -- earlier versions pile up:

   ```bash
   docker images clickhouse/clickhouse-server
   docker image rm clickhouse/clickhouse-server:<old tag> ...
   ```

2. Once ClickHouse is running again, empty its diagnostic logs (they are
   ClickHouse's own, not your data):

   ```bash
   docker exec llm-port-clickhouse sh -c \
     'for t in trace_log text_log metric_log asynchronous_metric_log; do
        clickhouse-client -u "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" \
          -q "TRUNCATE TABLE IF EXISTS system.$t"; done'
   ```

On the VM this took the disk from 100% to 51% used.

## Going back

Every upgrade leaves a backup in `<install_dir>/backups/<time>/`: a dump of
each database, the `.env`, and the migration versions it was taken at.

```bash
llmport restore <install_dir>/backups/<time>
```

It puts back every database and the `.env`, and restarts the services.
Checked on the upgraded VM: a chat deleted and a setting changed after the
backup were both back as they had been, and chat still answered.

To go back to the version you came from as well, check it out and run
`llmport upgrade --no-backup` to rebuild it.

A backup older than a cluster doesn't have that cluster in it, but the
machines are still running it. Take it over from **Clusters** instead of
recreating it. See [Taking over clusters your machines still run](taking-over-clusters.md).
