"""The unit file has to give up on a start that can never succeed.

The agent refuses to start when another one already holds the state lock --
correctly, because two agents on one node both enrol as the same machine and
commands dispatched to one never complete. But ``Restart=always`` with no
start limit turns that refusal into a loop: systemd retried every five
seconds, forever, while reporting

    Active: activating (auto-restart) (Result: exit-code)

which reads like a slow boot rather than a wedged service. Found on the DGX
head at **restart counter 9259** -- roughly fifteen hours of looping that
nothing anywhere surfaced, while the machine ran an older agent the whole
time.

A start limit is safe here for a specific reason: the agent handles a backend
outage itself, reconnecting with exponential backoff and never exiting for
it. So a failed *start* is always structural, never transient connectivity --
and giving up is the honest response to it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UNIT = (
    Path(__file__).resolve().parents[1]
    / "deploy/systemd/llmport-agent.service"
)


def _section(text: str, name: str) -> str:
    """The body of one ``[Section]`` from a unit file."""
    match = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert match, f"no [{name}] section"
    return match.group(1)


@pytest.mark.skipif(not UNIT.exists(), reason="unit file not present")
class TestShippedUnit:
    def test_it_stops_retrying_eventually(self) -> None:
        text = UNIT.read_text(encoding="utf-8")
        assert "StartLimitBurst=" in text
        assert "StartLimitIntervalSec=" in text

    def test_the_limit_is_in_the_section_systemd_reads_it_from(self) -> None:
        """``StartLimit*`` belongs to ``[Unit]``.

        Put in ``[Service]`` it is silently ignored on current systemd -- the
        unit parses, the service runs, and the loop is exactly as before.
        """
        text = UNIT.read_text(encoding="utf-8")
        unit = _section(text, "Unit")
        service = _section(text, "Service")
        assert "StartLimitBurst=" in unit
        assert "StartLimitBurst=" not in service
        assert "StartLimitIntervalSec=" in unit
        assert "StartLimitIntervalSec=" not in service

    def test_it_still_restarts_a_healthy_agent_that_dies(self) -> None:
        """The limit must not turn into "never restart".

        A crash after hours of running is exactly what ``Restart=always`` is
        for, and the interval window means those do not accumulate.
        """
        text = UNIT.read_text(encoding="utf-8")
        assert "Restart=always" in text

    def test_the_window_allows_a_slow_boot(self) -> None:
        """Docker may not be ready the instant the unit starts.

        ``After=docker.service`` mostly covers it, but the burst has to leave
        room for a few real retries or a reboot becomes a failed agent.
        """
        text = UNIT.read_text(encoding="utf-8")
        burst = int(re.search(r"StartLimitBurst=(\d+)", text).group(1))
        interval = int(re.search(r"StartLimitIntervalSec=(\d+)", text).group(1))
        restart_sec = int(re.search(r"RestartSec=(\d+)", text).group(1))

        assert burst >= 5, "too few retries to survive a slow boot"
        # The burst must fit inside the window, or the limit can never trip.
        assert burst * restart_sec < interval


def test_the_generated_unit_matches_the_shipped_one() -> None:
    """The installer has an inline fallback for hosts without the template.

    It drifted from the shipped file before; a node installed by the fallback
    would keep the old looping behaviour with nothing to show for it.
    """
    import llm_port_node_agent.__main__ as cli

    source = Path(cli.__file__).read_text(encoding="utf-8")
    # The fallback is a literal in `_build_service_content`.
    start = source.index("def _build_service_content")
    body = source[start : source.index("\ndef ", start + 1)]
    assert '"StartLimitIntervalSec=300\\n"' in body
    assert '"StartLimitBurst=10\\n\\n"' in body
    assert '"Restart=always\\n"' in body
