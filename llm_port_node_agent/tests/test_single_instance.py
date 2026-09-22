"""A second agent on one node must refuse to start.

What two agents on one machine actually do: both enrol as the same node, both
hold a stream, and the backend counts two live sessions. Commands dispatched
to the session that is not the one doing the work sit at ``dispatched``
forever.

The operator sees nothing happen. A scale to two replicas is accepted, the
deployment is queued, the reconciler runs — and every command it issues hangs,
so the screens waiting on those calls stall too. Nothing is logged as an
error, because from each component's own point of view nothing failed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from llm_port_node_agent.single_instance import (
    AlreadyRunningError,
    SingleInstanceLock,
)


def test_the_first_agent_takes_the_lock(tmp_path: Path) -> None:
    lock = SingleInstanceLock(tmp_path / "state.json")
    lock.acquire()
    try:
        assert lock.path.exists()
        assert lock.path.name == "state.lock"
    finally:
        lock.release()


def test_a_second_agent_is_refused(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    first = SingleInstanceLock(state)
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError) as err:
            SingleInstanceLock(state).acquire()
        # The message has to say what to do; "already running" alone sends
        # the operator looking for a crash that did not happen.
        assert "Stop the running agent first" in str(err.value)
        assert str(state.with_suffix(".lock")) in str(err.value)
    finally:
        first.release()


def test_releasing_lets_the_next_one_in(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    first = SingleInstanceLock(state)
    first.acquire()
    first.release()

    second = SingleInstanceLock(state)
    second.acquire()  # must not raise
    second.release()


def test_it_is_a_context_manager(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    with SingleInstanceLock(state):
        with pytest.raises(AlreadyRunningError):
            SingleInstanceLock(state).acquire()
    # Left the block: the next agent may start.
    SingleInstanceLock(state).acquire()


def test_separate_nodes_do_not_block_each_other(tmp_path: Path) -> None:
    """Two agents on one *host* are fine if they own different state."""
    a = SingleInstanceLock(tmp_path / "node-a" / "state.json")
    b = SingleInstanceLock(tmp_path / "node-b" / "state.json")
    a.acquire()
    b.acquire()  # must not raise
    a.release()
    b.release()


@pytest.mark.skipif(os.name == "nt", reason="signal semantics differ on Windows")
def test_a_killed_holder_releases_the_lock(tmp_path: Path) -> None:
    """Why this is an OS lock and not a pidfile.

    A pidfile survives SIGKILL and then blocks the next honest start, which
    turns one crash into a permanently un-startable agent.
    """
    state = tmp_path / "state.json"
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        from llm_port_node_agent.single_instance import SingleInstanceLock
        lock = SingleInstanceLock({str(state)!r})
        lock.acquire()
        print("held", flush=True)
        time.sleep(60)
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(AlreadyRunningError):
            SingleInstanceLock(state).acquire()
    finally:
        holder.kill()
        holder.wait(timeout=10)

    # The kernel dropped it with the process.
    survivor = SingleInstanceLock(state)
    survivor.acquire()
    survivor.release()
