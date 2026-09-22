"""One agent per node state, enforced by the operating system.

Two agents running against the same state directory both enrol as the same
node, both open a stream to the backend, and both are counted as live
sessions.  The backend then dispatches a command to one of them; commands that
land on the other session sit at ``dispatched`` and never reach a terminal
state.

The visible symptom is nothing happening: a scale to two replicas is recorded,
the deployment is queued, the reconciler runs, and every command it issues
hangs.  Meanwhile the screens waiting on those calls stall.  It was one stray
``nohup`` that caused it here, but a systemd unit racing a manual run, or two
installs sharing a state path, produce exactly the same thing.

An OS-level exclusive lock is used rather than a pidfile because the kernel
releases it when the holder dies, however it dies.  A pidfile survives SIGKILL
and then blocks the *next* honest start.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    """Another agent already holds this node's state."""


class SingleInstanceLock:
    """Exclusive, non-blocking lock on a node's state directory.

    Released automatically when the process exits, including on a crash, so a
    stale lock never blocks a restart.
    """

    def __init__(self, state_path: Path) -> None:
        self._path = Path(state_path).with_suffix(".lock")
        self._handle: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        """Take the lock, or raise :class:`AlreadyRunningError`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+")
        # Always lock the same byte.  Windows locks a range starting at the
        # current position, and "a+" opens at end-of-file -- so without this
        # seek two processes lock two different bytes and both succeed.
        handle.seek(0)
        try:
            _lock_exclusive(handle)
        except OSError as exc:
            handle.close()
            raise AlreadyRunningError(
                f"another llmport-agent already holds {self._path}. "
                "Two agents on one node both enrol as the same machine, and "
                "commands dispatched to the other one never complete. "
                "Stop the running agent first."
            ) from exc

        # The file stays empty on purpose: the locked byte is the whole
        # mechanism, and writing a pid into it would mean writing through the
        # region the lock covers.  Which process holds it is a question for
        # ``ps``; the message above says what to do about it.
        self._handle = handle
        log.debug("Holding single-instance lock %s (pid %s)", self._path, os.getpid())

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            _unlock(self._handle)
        except OSError:  # pragma: no cover - releasing is best effort
            pass
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


if os.name == "nt":  # pragma: no cover - platform split

    import msvcrt

    def _lock_exclusive(handle: IO[str]) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[str]) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:

    import fcntl

    def _lock_exclusive(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
