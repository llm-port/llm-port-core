"""Where a Ray deployment's logs actually are.

The logs panel showed nothing for a deployment that was serving. Not an
error, not a spinner -- an empty page, which reads as "this deployment is
silent" rather than "this reader is looking in the wrong place".

It was looking at the container's console. Under the Ray driver the runtime
container idles on ``sleep infinity`` and every Serve replica inside it writes
to its own file under ``/tmp/ray/session_latest/logs/serve/``, so the console
is legitimately and permanently empty. The per-model-container path, where the
container *is* the model server, still needs the console -- so both have to
work, and which one applies is decided by what is there rather than by a flag
somebody has to remember to set.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_port_node_agent.runtime_manager import RuntimeManager

APP = "llmport-b64e389b-58ae-4b1d-a754-1f9f48e22220"
SERVE_DIR = "/tmp/ray/session_latest/logs/serve"

REPLICA_OUTPUT = (
    f"===== replica_{APP}_LLMServer_Qwen2_5-0_5B-Instruct_wib8cvxx.log =====\n"
    "INFO 2026-09-21 22:08:41 Started vLLM engine\n"
    "INFO 2026-09-21 22:08:44 Adding request chatcmpl-1\n"
)


class _Runtime:
    """A container handler that answers `exec_` and `logs` from a script."""

    def __init__(
        self,
        *,
        serve_output: str = "",
        serve_rc: int = 0,
        console: str = "",
        exec_raises: bool = False,
    ) -> None:
        self._serve_output = serve_output
        self._serve_rc = serve_rc
        self._console = console
        self._exec_raises = exec_raises
        self.scripts: list[str] = []
        self.console_reads = 0

    @property
    def name(self) -> str:
        return "docker"

    async def exists(self, name: str) -> bool:
        return True

    async def inspect(self, name: str, *, format_: str | None = None, timeout_sec: float = 10):
        return {"State": {"Running": True}}

    async def exec_(
        self,
        name: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        stdin: str | None = None,
        timeout_sec: float = 120,
        raise_on_error: bool = True,
    ) -> tuple[int, str, str]:
        self.scripts.append(command[-1])
        if self._exec_raises:
            raise RuntimeError("container went away")
        return self._serve_rc, self._serve_output, ""

    async def logs(self, name: str, *, tail: str = "100", timestamps: bool = False):
        self.console_reads += 1
        return 0, self._console


def _manager(runtime: _Runtime) -> RuntimeManager:
    manager = RuntimeManager.__new__(RuntimeManager)
    manager._runtime = runtime  # noqa: SLF001 - the seam under test
    return manager


async def _read(manager: RuntimeManager, **overrides: Any) -> dict[str, Any]:
    payload = {
        "runtime_id": "b64e389b-58ae-4b1d-a754-1f9f48e22220",
        "runtime_name": APP,
        "container_name": "llm-port-ray-runtime",
        "tail": 50,
    }
    payload.update(overrides)
    manager._require_runtime_id = lambda p: p["runtime_id"]  # noqa: SLF001
    manager._lookup_container = lambda *_a, **_k: "llm-port-ray-runtime"  # noqa: SLF001
    return await manager.fetch_container_logs(payload)


# ── the regression ───────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_serving_deployment_is_no_longer_silent() -> None:
    runtime = _Runtime(serve_output=REPLICA_OUTPUT, console="")
    result = await _read(_manager(runtime))

    assert "Started vLLM engine" in result["logs"]
    assert runtime.console_reads == 0, "the console has nothing and was asked anyway"


@pytest.mark.anyio()
async def test_the_reader_looks_where_ray_writes() -> None:
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime))

    script = runtime.scripts[0]
    assert f"{SERVE_DIR}/replica_{APP}_*.log" in script
    assert "tail -n 50" in script


@pytest.mark.anyio()
async def test_one_replica_can_be_singled_out() -> None:
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime), replica_id="wib8cvxx")

    assert "wib8cvxx" in runtime.scripts[0]


@pytest.mark.anyio()
async def test_the_newest_replicas_come_first_and_the_list_is_capped() -> None:
    """A long-lived deployment accumulates a file per replica ever started.

    Reading all of them would return mostly dead replicas, oldest included,
    and could be megabytes.
    """
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime))

    script = runtime.scripts[0]
    assert "ls -1t" in script, "newest first"
    assert "head -8" in script, "an unbounded read is not a log page"


@pytest.mark.anyio()
async def test_each_replicas_output_is_labelled() -> None:
    """Several replicas in one page are unreadable if nothing says which is which."""
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime))
    assert "basename" in runtime.scripts[0]


# ── falling back rather than reporting an empty page ─────────────────────


@pytest.mark.anyio()
async def test_a_container_that_is_its_own_model_server_still_uses_the_console() -> None:
    """The per-model-container path, which must not regress.

    There are no Serve replica files there, so the glob finds nothing and the
    console is the right answer.
    """
    runtime = _Runtime(serve_rc=9, serve_output="", console="vLLM API server started\n")
    result = await _read(_manager(runtime))

    assert result["logs"] == "vLLM API server started\n"
    assert runtime.console_reads == 1


@pytest.mark.anyio()
async def test_a_deployment_with_no_replica_yet_falls_back() -> None:
    """Between `serve.run` and the first replica there are no files at all."""
    runtime = _Runtime(serve_rc=9, serve_output="", console="waiting\n")
    result = await _read(_manager(runtime))
    assert result["logs"] == "waiting\n"


@pytest.mark.anyio()
async def test_an_exec_failure_falls_back_instead_of_raising() -> None:
    """A log read must never be the thing that fails a command."""
    runtime = _Runtime(exec_raises=True, console="console still readable\n")
    result = await _read(_manager(runtime))
    assert result["logs"] == "console still readable\n"


@pytest.mark.anyio()
async def test_a_reply_of_only_whitespace_is_not_treated_as_content() -> None:
    runtime = _Runtime(serve_output="   \n\n", console="from the console\n")
    result = await _read(_manager(runtime))
    assert result["logs"] == "from the console\n"


# ── the name reaches a shell ─────────────────────────────────────────────


@pytest.mark.anyio()
@pytest.mark.parametrize(
    "hostile",
    [
        "app; rm -rf /",
        "app$(id)",
        "app`id`",
        "app | cat /etc/shadow",
        "app' ; echo pwned ; '",
        "../../../etc",
    ],
)
async def test_a_name_that_could_end_the_glob_is_refused(hostile: str) -> None:
    """The glob must reach the shell unquoted to expand, so the name is
    checked against an alphabet rather than escaped into one.

    These are our own generated identifiers in practice; the check is here
    because "in practice" is not a property the shell enforces.
    """
    runtime = _Runtime(serve_output=REPLICA_OUTPUT, console="console\n")
    result = await _read(_manager(runtime), runtime_name=hostile)

    assert runtime.scripts == [], "nothing was run inside the container"
    assert result["logs"] == "console\n"


@pytest.mark.anyio()
async def test_a_hostile_replica_id_is_dropped_not_refused() -> None:
    """The app's logs are still worth showing; only the filter is discarded."""
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime), replica_id="x; rm -rf /")

    script = runtime.scripts[0]
    assert "rm -rf" not in script
    assert f"replica_{APP}_*.log" in script


@pytest.mark.anyio()
async def test_our_real_app_names_and_replica_ids_are_accepted() -> None:
    """The alphabet must not reject the values actually in use."""
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime), replica_id="wib8cvxx")
    assert runtime.scripts, "a legitimate name was refused"


# ── the tail bound ───────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_an_absurd_tail_is_clamped() -> None:
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    await _read(_manager(runtime), tail=10_000_000)
    script = runtime.scripts[0]
    tail_value = int(script.split("tail -n ")[1].split()[0])
    assert tail_value <= 5000


@pytest.mark.anyio()
async def test_a_nonsense_tail_does_not_break_the_read() -> None:
    runtime = _Runtime(serve_output=REPLICA_OUTPUT)
    result = await _read(_manager(runtime), tail="not a number")
    assert "Started vLLM engine" in result["logs"]
