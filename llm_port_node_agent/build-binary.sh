#!/bin/sh
# Build the single-file agent for one Linux platform.
#
# PyInstaller does not cross-compile: a linux-aarch64 build has to happen on
# linux-aarch64. A container gives that when the host can emulate the target,
# and it also pins the glibc the binary links against -- built on a newer
# distro than the node runs, the agent fails there with a GLIBC version error
# and nothing about that says "rebuild me somewhere older".
#
# When the host cannot produce the target at all -- an x86_64 workstation with
# no arm64 emulation, which is the usual case -- build it on a machine that
# is the target. --remote copies the source there, builds, and brings the
# binary back, leaving nothing behind.
#
#   sh build-binary.sh                             # this machine's arch
#   sh build-binary.sh linux-aarch64               # explicit, needs emulation
#   sh build-binary.sh linux-aarch64 --remote sachi@10.88.10.49
#   sh build-binary.sh linux-aarch64 --remote sachi@10.88.10.49 \
#        --ssh-key ~/.ssh/id_dgx_spark
#
# The result lands in dist/, named the way /api/install/plan expects:
#
#   dist/llmport-agent-linux-aarch64
#
# That is the whole publish step. A backend running from this checkout reads
# dist/ directly, so every node install -- online or air-gapped -- verifies
# against the file you just built. There is no second copy to keep in step;
# there used to be, and the one being served fell a build behind in silence.
# A real deployment has no checkout, and points `agent_binary_dir` at
# wherever its binaries were placed.
set -eu

PLATFORM=""
REMOTE=""
SSH_KEY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --remote) REMOTE="${2:-}"; shift ;;
    --ssh-key) SSH_KEY="${2:-}"; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; exit 2 ;;
    *) PLATFORM="$1" ;;
  esac
  shift
done

if [ -z "$PLATFORM" ]; then
  case "$(uname -m)" in
    x86_64|amd64) PLATFORM="linux-x86_64" ;;
    aarch64|arm64) PLATFORM="linux-aarch64" ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 2 ;;
  esac
fi

case "$PLATFORM" in
  linux-x86_64) DOCKER_PLATFORM="linux/amd64" ;;
  linux-aarch64) DOCKER_PLATFORM="linux/arm64" ;;
  *) echo "Unknown platform: $PLATFORM" >&2; exit 2 ;;
esac

SRC=$(cd "$(dirname "$0")" && pwd)
ARTIFACT="llmport-agent-$PLATFORM"

# The build itself, as one string, so the local and remote paths run exactly
# the same thing. A difference between them would show up as a binary that
# works in one place and not the other, which is the hardest kind to chase.
build_script() {
  cat <<BUILD
set -eu
# Git Bash rewrites anything that looks like a POSIX path in an argument, so
# "-w /src" reached the daemon as "C:/Program Files/Git/src" and every local
# build on Windows died before starting. MSYS_NO_PATHCONV stops that; it is
# an unset variable everywhere else, so the remote path is unchanged.
# The bind source has to go the other way -- the daemon wants C:/... , which
# is what "pwd -W" gives and which fails harmlessly into plain pwd on Linux.
SRCDIR=\$(pwd -W 2>/dev/null || pwd)
MSYS_NO_PATHCONV=1 docker run --rm --platform "$DOCKER_PLATFORM" -v "\$SRCDIR:/src" -w /src \\
  python:3.12-slim-bookworm sh -c '
    set -eu
    # PyInstaller reads the ELF it is bundling: without objdump it stops with
    # "On Linux, objdump is required", which is a build-host problem rather
    # than anything to do with the agent.
    apt-get update -qq && apt-get install -y -qq binutils >/dev/null
    pip install --quiet --no-cache-dir pyinstaller .
    pyinstaller --clean --noconfirm node_agent.spec
    mv dist/llmport-agent "dist/$ARTIFACT"
    # Hand the output back to whoever owns the source.
    #
    # The container is root and the source is a bind mount, so everything it
    # writes -- dist/, build/, the caches -- lands root-owned on the host.
    # On a remote build that left a directory the invoking user could not
    # delete, and the cleanup failed with nothing but "Permission denied".
    chown -R "\$(stat -c "%u:%g" /src)" /src
  '
BUILD
}

# ── local ──────────────────────────────────────────────────────────────────

if [ -z "$REMOTE" ]; then
  cd "$SRC"
  build_script | sh
  echo
  echo "Built: $SRC/dist/$ARTIFACT"
  sha256sum "$SRC/dist/$ARTIFACT" 2>/dev/null || shasum -a 256 "$SRC/dist/$ARTIFACT"
  exit 0
fi

# ── remote ─────────────────────────────────────────────────────────────────

SSH="ssh -o StrictHostKeyChecking=accept-new"
SCP="scp -o StrictHostKeyChecking=accept-new"
if [ -n "$SSH_KEY" ]; then
  SSH="$SSH -i $SSH_KEY"
  SCP="$SCP -i $SSH_KEY"
fi

echo "Building $PLATFORM on $REMOTE"

# Check before copying anything: a missing docker on the far end is a clearer
# failure here than half way through a build.
$SSH "$REMOTE" 'command -v docker >/dev/null' || {
  echo "ERROR: no docker on $REMOTE -- the build needs it." >&2
  exit 1
}

REMOTE_ARCH=$($SSH "$REMOTE" 'uname -m')
case "$PLATFORM:$REMOTE_ARCH" in
  linux-aarch64:aarch64|linux-aarch64:arm64|linux-x86_64:x86_64|linux-x86_64:amd64) ;;
  *)
    # Not fatal: the far end may emulate. Say so rather than silently
    # producing a binary for the wrong machine.
    echo "NOTE: $REMOTE is $REMOTE_ARCH, building $PLATFORM -- relies on emulation there."
    ;;
esac

REMOTE_DIR=$($SSH "$REMOTE" 'mktemp -d -t llmport-build-XXXXXX')

# Clean up, and say so when it does not work.
#
# This was a trap ending in "|| true", which is how 36MB of source was left
# on a machine that had only lent us a CPU -- silently, because the one thing
# that would have reported it was the thing being suppressed.
cleanup_remote() {
  $SSH "$REMOTE" "rm -rf '$REMOTE_DIR'" >/dev/null 2>&1
  if $SSH "$REMOTE" "test -d '$REMOTE_DIR'" >/dev/null 2>&1; then
    echo "WARNING: could not remove $REMOTE:$REMOTE_DIR -- remove it by hand." >&2
    return 1
  fi
  return 0
}
# Only for the abnormal paths; the normal one calls it explicitly below, so a
# successful build reports a failed cleanup instead of exiting 0 regardless.
trap 'cleanup_remote >/dev/null 2>&1 || true' INT TERM

echo "  syncing source → $REMOTE:$REMOTE_DIR"
# tar over ssh rather than rsync: rsync is not on a Git Bash install and not
# guaranteed on a minimal node, ssh and tar are on both by definition.
# The excludes matter -- .venv alone is hundreds of megabytes and useless on
# the far side, and dist/ would ship a binary for the wrong architecture.
tar -C "$SRC" \
  --exclude=.venv --exclude=dist --exclude=build \
  --exclude=__pycache__ --exclude='*.pyc' --exclude=.git \
  -czf - . | $SSH "$REMOTE" "tar -C $REMOTE_DIR -xzf -"

echo "  building…"
if ! build_script | $SSH "$REMOTE" "cd $REMOTE_DIR && sh"; then
  echo "ERROR: the build failed on $REMOTE." >&2
  cleanup_remote || true
  exit 1
fi

echo "  fetching artifact"
mkdir -p "$SRC/dist"
if ! $SCP "$REMOTE:$REMOTE_DIR/dist/$ARTIFACT" "$SRC/dist/$ARTIFACT"; then
  echo "ERROR: built on $REMOTE but could not fetch $ARTIFACT." >&2
  echo "       Left $REMOTE:$REMOTE_DIR in place so it can be copied by hand." >&2
  exit 1
fi

echo "  cleaning up $REMOTE"
cleanup_remote || true

echo
echo "Built on $REMOTE, fetched to: $SRC/dist/$ARTIFACT"
sha256sum "$SRC/dist/$ARTIFACT" 2>/dev/null || shasum -a 256 "$SRC/dist/$ARTIFACT"
