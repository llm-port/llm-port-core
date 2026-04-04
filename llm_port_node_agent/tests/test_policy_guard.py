from llm_port_node_agent.policy_guard import PolicyGuard, PolicyViolationError
from llm_port_node_agent.state_store import AgentState


def test_policy_blocks_deploy_when_in_maintenance() -> None:
    guard = PolicyGuard()
    state = AgentState(maintenance_mode=True)
    try:
        guard.validate(command_type="deploy_workload", state=state)
        assert False, "Expected PolicyViolationError"
    except PolicyViolationError:
        assert True


def test_policy_allows_stop_in_maintenance() -> None:
    guard = PolicyGuard()
    state = AgentState(maintenance_mode=True)
    guard.validate(command_type="stop_workload", state=state)
