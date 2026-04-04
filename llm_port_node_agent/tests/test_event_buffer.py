"""Tests for EventBuffer."""

from llm_port_node_agent.event_buffer import EventBuffer


def test_add_and_drain() -> None:
    buf = EventBuffer()
    buf.add(event_type="test.event", payload={"key": "value"})
    buf.add(event_type="test.event2", severity="error", payload={"key": "v2"})
    batch = buf.drain(max_items=10)
    assert len(batch) == 2
    assert batch[0]["event_type"] == "test.event"
    assert batch[0]["severity"] == "info"
    assert batch[1]["severity"] == "error"
    assert "ts" in batch[0]


def test_drain_empty() -> None:
    buf = EventBuffer()
    assert buf.drain() == []


def test_drain_respects_max_items() -> None:
    buf = EventBuffer()
    for i in range(10):
        buf.add(event_type=f"evt.{i}", payload={})
    first = buf.drain(max_items=3)
    assert len(first) == 3
    second = buf.drain(max_items=20)
    assert len(second) == 7
    assert buf.drain() == []
