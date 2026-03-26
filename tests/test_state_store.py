from pathlib import Path
from unittest.mock import patch

from llm_port_node_agent.config import _default_state_path
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


@patch("llm_port_node_agent.config.os.getuid", create=True, return_value=0)
@patch("llm_port_node_agent.config.sys")
def test_default_state_path_linux_root(mock_sys, _mock_uid) -> None:
    mock_sys.platform = "linux"
    assert _default_state_path() == "/var/lib/llmport-agent/state.json"


@patch("llm_port_node_agent.config.Path.home", return_value=Path("/home/testuser"))
@patch("llm_port_node_agent.config.os.getuid", create=True, return_value=1000)
@patch("llm_port_node_agent.config.sys")
def test_default_state_path_linux_non_root(mock_sys, _mock_uid, _mock_home) -> None:
    mock_sys.platform = "linux"
    result = _default_state_path()
    expected = str(Path("/home/testuser/.local/share/llmport-agent/state.json"))
    assert result == expected


@patch("llm_port_node_agent.config.sys")
@patch.dict("os.environ", {"PROGRAMDATA": r"C:\ProgramData"})
def test_default_state_path_windows(mock_sys) -> None:
    mock_sys.platform = "win32"
    result = _default_state_path()
    assert "llmport-agent" in result
    assert result.endswith("state.json")
    assert "ProgramData" in result
