"""Reclaiming a dev port from a process nothing can recognise.

uvicorn and taskiq spawn their workers through multiprocessing, which re-execs
as ``python -c "from multiprocessing.spawn import spawn_main; ..."``. That
command line carries no module name and no workspace path, and on the machine
where this was found it was not even the workspace's interpreter -- it was the
system Python. Twelve such orphans were serving port 8000 with every parent
long dead, answering requests from another host.

No name- or path-based filter can find those. What identifies them is that
they are sitting on the port the service about to start needs.
"""

from __future__ import annotations

from collections import namedtuple

import psutil
import pytest

from llmport.commands.dev import dev_up

_Addr = namedtuple("_Addr", "ip port")
_Conn = namedtuple("_Conn", "status pid laddr")

PORTS = {8000: "Backend", 8001: "API gateway"}


class _FakeProc:
    def __init__(
        self,
        pid: int,
        name: str = "python.exe",
        parents: list["_FakeProc"] | None = None,
        children: list["_FakeProc"] | None = None,
    ) -> None:
        self.pid = pid
        self._name = name
        self._parents = parents or []
        self._children = children or []
        self.killed = False

    def name(self) -> str:
        return self._name

    def parents(self) -> list["_FakeProc"]:
        return self._parents

    def children(self, recursive: bool = False) -> list["_FakeProc"]:
        return self._children

    def kill(self) -> None:
        self.killed = True


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    conns: list[_Conn],
    procs: dict[int, _FakeProc],
    own: set[int] | None = None,
) -> None:
    monkeypatch.setattr(dev_up.psutil, "net_connections", lambda kind: conns)
    monkeypatch.setattr(dev_up, "_own_process_chain", lambda: own or set())

    def _process(pid: int) -> _FakeProc:
        if pid not in procs:
            raise psutil.NoSuchProcess(pid)
        return procs[pid]

    monkeypatch.setattr(dev_up.psutil, "Process", _process)


def test_stops_an_orphan_no_filter_could_name(monkeypatch: pytest.MonkeyPatch) -> None:
    orphan = _FakeProc(4242)
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_LISTEN, 4242, _Addr("0.0.0.0", 8000))],
        {4242: orphan},
    )

    assert dev_up._stop_port_holders(PORTS) == [4242]
    assert orphan.killed is True


def test_stops_every_holder_of_a_shared_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows lets several sockets sit on one port; one survivor still serves."""
    procs = {pid: _FakeProc(pid) for pid in (10, 11, 12)}
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_LISTEN, pid, _Addr("0.0.0.0", 8000)) for pid in procs],
        procs,
    )

    assert dev_up._stop_port_holders(PORTS) == [10, 11, 12]
    assert all(p.killed for p in procs.values())


def test_ignores_ports_we_do_not_own(monkeypatch: pytest.MonkeyPatch) -> None:
    other = _FakeProc(50)
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_LISTEN, 50, _Addr("0.0.0.0", 5432))],
        {50: other},
    )

    assert dev_up._stop_port_holders(PORTS) == []
    assert other.killed is False


def test_ignores_connections_that_are_not_listening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outbound connection to 8000 is a client, not the server."""
    client = _FakeProc(60)
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_ESTABLISHED, 60, _Addr("127.0.0.1", 8000))],
        {60: client},
    )

    assert dev_up._stop_port_holders(PORTS) == []
    assert client.killed is False


def test_never_stops_its_own_process_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = _FakeProc(77)
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_LISTEN, 77, _Addr("0.0.0.0", 8000))],
        {77: mine},
        own={77},
    )

    assert dev_up._stop_port_holders(PORTS) == []
    assert mine.killed is False


@pytest.mark.parametrize("pid", [0, 4])
def test_never_touches_the_system_pids(
    monkeypatch: pytest.MonkeyPatch, pid: int
) -> None:
    system = _FakeProc(pid, "System")
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_LISTEN, pid, _Addr("0.0.0.0", 8000))],
        {pid: system},
    )

    assert dev_up._stop_port_holders(PORTS) == []
    assert system.killed is False


def test_a_holder_that_exits_first_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [_Conn(psutil.CONN_LISTEN, 99, _Addr("0.0.0.0", 8000))], {})

    assert dev_up._stop_port_holders(PORTS) == []


def test_reports_each_one_rather_than_stopping_silently(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Killing something unnamed must be visible, not a quiet side effect."""
    _patch(
        monkeypatch,
        [_Conn(psutil.CONN_LISTEN, 4242, _Addr("0.0.0.0", 8000))],
        {4242: _FakeProc(4242)},
    )

    dev_up._stop_port_holders(PORTS)

    out = capsys.readouterr().out
    assert "4242" in out
    assert "8000" in out


def test_survives_a_platform_that_denies_the_connection_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _denied(kind: str) -> list[_Conn]:
        raise psutil.AccessDenied(None)

    monkeypatch.setattr(dev_up.psutil, "net_connections", _denied)
    monkeypatch.setattr(dev_up, "_own_process_chain", lambda: set())

    assert dev_up._stop_port_holders(PORTS) == []


class TestSupervisorTree:
    """Killing the worker is not enough when something restarts it.

    The backend runs under a reload supervisor: remove the process holding
    the port and the parent starts another on the same port, so the reclaim
    reports failure against a pid that no longer exists.
    """

    def test_climbs_to_the_supervisor_and_takes_the_tree(self) -> None:
        worker = _FakeProc(30, "python.exe")
        supervisor = _FakeProc(20, "uv.exe", children=[worker])
        shell = _FakeProc(10, "cmd.exe", children=[supervisor, worker])
        worker._parents = [supervisor, shell]
        supervisor._parents = [shell]

        tree = dev_up._supervisor_tree(worker, set())

        assert [p.pid for p in tree] == [worker.pid, supervisor.pid, shell.pid]

    def test_stops_climbing_at_an_unrelated_parent(self) -> None:
        """A terminal the operator owns is not part of the service."""
        worker = _FakeProc(31, "python.exe")
        explorer = _FakeProc(11, "explorer.exe", children=[worker])
        worker._parents = [explorer]

        assert [p.pid for p in dev_up._supervisor_tree(worker, set())] == [31]

    def test_never_climbs_into_its_own_chain(self) -> None:
        worker = _FakeProc(32, "python.exe")
        mine = _FakeProc(12, "uv.exe", children=[worker])
        worker._parents = [mine]

        tree = dev_up._supervisor_tree(worker, {12})

        assert [p.pid for p in tree] == [32]

    def test_kills_children_before_the_supervisor(self) -> None:
        """Otherwise the supervisor gets a chance to start a replacement."""
        worker = _FakeProc(33, "python.exe")
        supervisor = _FakeProc(23, "uv.exe", children=[worker])
        worker._parents = [supervisor]

        tree = dev_up._supervisor_tree(worker, set())

        assert tree[0].pid == 33
        assert tree[-1].pid == 23


class TestOrphanedWorkers:
    """The processes that actually held the port, and that nothing could see.

    multiprocessing re-execs a worker as `python -c "from
    multiprocessing.spawn import spawn_main; ..."`. Kill its supervisor and
    the worker survives holding the inherited listening socket, while the
    system connection table still names the dead supervisor as owner.
    """

    _WORKER = [
        "python.exe",
        "-c",
        "from multiprocessing.spawn import spawn_main; spawn_main(handle=7)",
    ]

    def _patch(self, monkeypatch, procs, alive):
        monkeypatch.setattr(dev_up.psutil, "process_iter", lambda _a: list(procs))
        monkeypatch.setattr(dev_up.psutil, "pid_exists", lambda pid: pid in alive)
        monkeypatch.setattr(dev_up, "_own_process_chain", lambda: set())

    def test_stops_a_worker_whose_supervisor_is_gone(self, monkeypatch) -> None:
        orphan = _FakeProc(4242)
        orphan.info = {"cmdline": self._WORKER, "ppid": 999}
        self._patch(monkeypatch, [orphan], alive=set())

        assert dev_up._stop_orphaned_workers() == [4242]
        assert orphan.killed is True

    def test_spares_a_worker_whose_supervisor_is_alive(self, monkeypatch) -> None:
        """A running service's own workers are not ours to reap."""
        worker = _FakeProc(4243)
        worker.info = {"cmdline": self._WORKER, "ppid": 500}
        self._patch(monkeypatch, [worker], alive={500})

        assert dev_up._stop_orphaned_workers() == []
        assert worker.killed is False

    def test_ignores_processes_that_are_not_workers(self, monkeypatch) -> None:
        other = _FakeProc(4244)
        other.info = {"cmdline": ["python.exe", "-m", "http.server"], "ppid": 1}
        self._patch(monkeypatch, [other], alive=set())

        assert dev_up._stop_orphaned_workers() == []
        assert other.killed is False

    def test_never_stops_its_own_chain(self, monkeypatch) -> None:
        mine = _FakeProc(4245)
        mine.info = {"cmdline": self._WORKER, "ppid": 999}
        monkeypatch.setattr(dev_up.psutil, "process_iter", lambda _a: [mine])
        monkeypatch.setattr(dev_up.psutil, "pid_exists", lambda pid: False)
        monkeypatch.setattr(dev_up, "_own_process_chain", lambda: {4245})

        assert dev_up._stop_orphaned_workers() == []
        assert mine.killed is False
