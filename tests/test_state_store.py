from pathlib import Path

from llm_port_node_agent.state_store import StateStore


def test_state_store_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.state.credential = "abc"
    store.state.node_id = "node-1"
    seq = store.next_seq()
    assert seq == 1
    store.set_workload("runtime-1", {"container_name": "c1"})
    store.remember_command_result("cmd-1", {"success": True, "result": {"ok": True}})

    store2 = StateStore(tmp_path / "state.json")
    assert store2.state.credential == "abc"
    assert store2.state.node_id == "node-1"
    assert store2.state.tx_seq == 1
    assert store2.workload("runtime-1") == {"container_name": "c1"}
    assert store2.get_command_result("cmd-1") == {"success": True, "result": {"ok": True}}
