"""Whether a route's model answers with tool calls, as the gateway is told.

vLLM calls tools only when started with ``--enable-auto-tool-choice`` and a
``--tool-call-parser``; offered tools without them, it refuses the request.
The gateway offers its knowledge tools only to routes that say they can take
them, so every publisher has to say.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.inference import found as found_vllm
from llm_port_backend.services.llm.kinds import (
    argv_calls_tools,
    calls_tools,
    deployment_calls_tools,
    runtime_calls_tools,
)
from tests.test_inference_found import _embed, _Gateway, _machine


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"enable_auto_tool_choice": True, "tool_call_parser": "hermes"}, True),
        ({"--enable-auto-tool-choice": "", "--tool-call-parser": "llama3_json"}, True),
        ({"enable_auto_tool_choice": True}, False),  # vLLM refuses auto without a parser
        ({"tool_call_parser": "hermes"}, False),
        ({"enable_auto_tool_choice": False, "tool_call_parser": "hermes"}, False),
        ({}, False),
    ],
)
def test_the_flags_that_turn_tool_calls_on(flags: dict[str, Any], expected: bool) -> None:
    assert calls_tools(flags) is expected


def test_a_command_line_turns_them_on_with_both_flags() -> None:
    assert argv_calls_tools(["vllm", "serve", "m", "--enable-auto-tool-choice", "--tool-call-parser", "hermes"])
    assert argv_calls_tools("--tool-call-parser=hermes --enable-auto-tool-choice")
    assert not argv_calls_tools(["vllm", "serve", "m", "--enable-auto-tool-choice"])
    assert not argv_calls_tools(None)


def test_a_runtime_and_a_deployment_say_it_from_their_config() -> None:
    runtime = SimpleNamespace(
        generic_config={},
        provider_config={"engine_args": {"enable-auto-tool-choice": True, "tool-call-parser": "hermes"}},
    )
    assert runtime_calls_tools(runtime)
    assert runtime_calls_tools(SimpleNamespace(
        generic_config={}, provider_config={"extra_args": "--enable-auto-tool-choice --tool-call-parser hermes"},
    ))
    assert not runtime_calls_tools(SimpleNamespace(generic_config={}, provider_config={}))

    on = {"engine": {"name": "vllm", "config": {"enable_auto_tool_choice": True, "tool_call_parser": "hermes"}}}
    assert deployment_calls_tools(on)
    assert not deployment_calls_tools({"engine": {"name": "vllm", "config": {}}})


async def test_a_found_container_says_it_from_its_command_line(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def answers(url: str) -> dict[str, Any]:
        return {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}

    monkeypatch.setattr(found_vllm, "probe", answers)
    chat = _embed(
        name="Qwen-Chat", task="chat",
        args=["vllm", "serve", "Qwen/Qwen2.5-7B-Instruct", "--enable-auto-tool-choice", "--tool-call-parser", "hermes"],
    )
    node = await _machine(dbsession, [chat, _embed()])
    gateway = _Gateway()

    await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen-Chat", alias=f"c-{uuid.uuid4().hex[:6]}")
    await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias=f"e-{uuid.uuid4().hex[:6]}")
    assert [kw["tools"] for _, kw in gateway.calls] == [True, False]
