"""Phase 20 action fence and verified continuation tests."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.agent_state import AgentState
from core.orchestrator.state import (
    Action,
    GroundingMethod,
    OrchestratorState,
    RiskLevel,
    TargetSpec,
    VerificationMethod,
)
from core.orchestrator.task_engine import (
    ACTION_FENCE_BLOCKED_RUNTIME,
    ACTION_FENCE_BLOCKED_RECOVERY,
    ACTION_FENCE_BLOCKED_VERIFICATION,
    ACTION_HANDOFF_REQUIRED,
    TaskEngine,
)
from core.runtime_health import RuntimeHealthSupervisor


class _StatusBus:
    def __init__(self, records):
        self.records = {
            str(item["name"]): dict(item) for item in records
        }
        self.calls = 0

    def capability_status(self, payload=None):
        self.calls += 1
        return {
            "success": True,
            "data": {"capabilities": [dict(item)
                                      for item in self.records.values()]},
        }

    def set_state(self, name, state, failed=False):
        self.records[name] = {
            "name": name, "state": state, "ready": state in {"READY", "ACTIVE"},
            "failed": failed, "reason": f"{name} is {state.lower()}",
        }


class _ScreenMap:
    def __init__(self, rows=None, recover=True):
        self.rows = list(rows if rows is not None else [{"name": "ready"}])
        self.recover = recover
        self.refresh_calls = 0

    def available(self):
        return True

    def current_view(self, limit=10):
        return "ready"

    def refresh_sync(self):
        self.refresh_calls += 1
        if self.recover:
            self.rows = [{"name": "recovered"}]
        return self.recover


def _record(name, state="READY", failed=False):
    return {
        "name": name, "state": state, "ready": state in {"READY", "ACTIVE"},
        "failed": bool(failed or state == "FAILED"), "reason": "",
    }


class _Engine:
    def __init__(self, state, *, phase=OrchestratorState.ACTING,
                 step=0, completed=0, verified=False, action_result=None):
        self.state = state
        self.executor = SimpleNamespace(execute=AsyncMock())
        self.engine = TaskEngine(
            Mock(), Mock(), self.executor, Mock(), Mock(), agent_state=state)
        task = self.engine.start_task("fence test task")
        task.subgoals = [
            SimpleNamespace(
                description=f"step {index}",
                completed=index < completed,
            )
            for index in range(max(2, step + 1))
        ]
        task.current_step = step
        task.completed_steps = [
            SimpleNamespace(success=True) for _ in range(completed)
        ]
        task.current_application = "Notepad"
        task.current_window = {"app": "Notepad", "title": "Fence.txt"}
        task.last_observation = SimpleNamespace(
            is_fresh=lambda max_age=5.0: True,
        )
        self.action = Action(
            task_id=task.task_id,
            step_id=step,
            intent="perform the next safe action",
            action_type="click",
            target=TargetSpec(text_match="Save"),
            target_description="Save button",
            verification_method=VerificationMethod.CUSTOM,
            grounding_method=GroundingMethod.UIA,
            risk_level=RiskLevel.LOW,
        )
        task.last_action = self.action
        if action_result is not None:
            task.last_action_result = action_result
        task.verification_result = SimpleNamespace(
            success=verified,
            method=VerificationMethod.CUSTOM,
            evidence="verified" if verified else "not verified",
        )
        self.task = task
        self.engine._state = phase
        self.engine._running = True

    def set_bus(self, records):
        bus = _StatusBus(records)
        self.engine.set_capability_bus(bus)
        return bus


class ActionFenceTests(unittest.TestCase):
    def test_healthy_required_capability_allows_action(self):
        state = AgentState()
        fixture = _Engine(state, verified=True)
        bus = fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.state, "RELEASED")
        self.assertEqual(decision.decision, "ALLOW")
        self.assertEqual(bus.calls, 1)
        self.assertEqual(state.snapshot()["action_fence"]["decision"], "ALLOW")

    def test_degraded_required_capability_blocks_action_start(self):
        state = AgentState()
        fixture = _Engine(state)
        fixture.set_bus([
            _record("perception"), _record("computer_tools"),
            _record("uia", "DEGRADED"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, ACTION_FENCE_BLOCKED_RUNTIME)
        self.assertEqual(decision.decision, "BLOCK_RUNTIME")

    def test_failed_or_uncertain_capability_truth_blocks_action_start(self):
        for state_name, failed in (("FAILED", True), ("UNCERTAIN", False)):
            with self.subTest(state=state_name):
                state = AgentState()
                fixture = _Engine(state)
                fixture.set_bus([
                    _record("perception"), _record("computer_tools"),
                    _record("uia", state_name, failed=failed),
                ])
                decision = fixture.engine.check_action_fence(fixture.action)
                self.assertEqual(decision.state, ACTION_FENCE_BLOCKED_RUNTIME)
                self.assertFalse(decision.allowed)

    def test_active_runtime_handoff_blocks_action_start(self):
        state = AgentState()
        fixture = _Engine(state)
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        fixture.engine.pause_for_runtime_health("uia", "runtime degraded")
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertEqual(decision.state, ACTION_FENCE_BLOCKED_RECOVERY)
        self.assertEqual(decision.decision, "BLOCK_RECOVERY")

    def test_capability_ready_without_task_verification_blocks(self):
        state = AgentState()
        action_result = SimpleNamespace(action=None, success=True)
        fixture = _Engine(state, verified=False, action_result=action_result)
        fixture.task.last_action_result.action = fixture.action
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertEqual(decision.state, ACTION_FENCE_BLOCKED_VERIFICATION)
        self.assertEqual(decision.decision, "BLOCK_VERIFICATION")

    def test_partial_action_is_not_replayed(self):
        state = AgentState()
        action_result = SimpleNamespace(action=None, success=True)
        fixture = _Engine(state, verified=False, action_result=action_result)
        fixture.task.last_action_result.action = fixture.action
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision, "BLOCK_VERIFICATION")
        fixture.executor.execute.assert_not_awaited()

    def test_blocked_do_acting_does_not_execute_or_advance(self):
        state = AgentState()
        action_result = SimpleNamespace(action=None, success=True)
        fixture = _Engine(state, verified=False, action_result=action_result)
        fixture.task.last_action_result.action = fixture.action
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        asyncio.run(fixture.engine._do_acting())
        fixture.executor.execute.assert_not_awaited()
        self.assertEqual(fixture.task.current_step, 0)
        self.assertEqual(fixture.task.completed_steps, [])
        self.assertFalse(fixture.task.verification_result.success)
        self.assertEqual(fixture.engine.state, OrchestratorState.ACTING)

    def test_verified_continuation_allows_exactly_next_action(self):
        state = AgentState()
        fixture = _Engine(state, step=1, completed=1, verified=True)
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.verification_basis, "TaskState completed_steps")

        fixture.executor.execute.return_value = SimpleNamespace(
            success=True, grounded_coordinates=None, message="executed",
            error="", duration_seconds=0.0, actual_result=None,
        )
        asyncio.run(fixture.engine._do_acting())
        fixture.executor.execute.assert_awaited_once()
        self.assertEqual(fixture.task.current_step, 1)
        self.assertEqual(len(fixture.task.completed_steps), 1)

    def test_already_verified_current_action_is_handoff_not_replay(self):
        state = AgentState()
        action_result = SimpleNamespace(action=None, success=True)
        fixture = _Engine(state, verified=True, action_result=action_result)
        fixture.task.last_action_result.action = fixture.action
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertEqual(decision.decision, ACTION_HANDOFF_REQUIRED)
        self.assertEqual(decision.state, ACTION_FENCE_BLOCKED_VERIFICATION)
        fixture.executor.execute.assert_not_awaited()

    def test_repeated_blocked_checks_are_bounded_and_deduplicated(self):
        state = AgentState()
        fixture = _Engine(state)
        fixture.set_bus([
            _record("perception"), _record("computer_tools"),
            _record("uia", "DEGRADED"),
        ])
        for _ in range(200):
            decision = fixture.engine.check_action_fence(fixture.action)
        self.assertFalse(decision.allowed)
        self.assertEqual(len(state.snapshot()["action_fence"]), 10)
        self.assertNotIn("history", state.snapshot()["action_fence"])
        self.assertEqual(fixture.executor.execute.await_count, 0)

    def test_ready_idle_verification_owner_is_not_a_runtime_handoff(self):
        state = AgentState()
        fixture = _Engine(state)
        bus = fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
            {"name": "verification", "state": "INACTIVE", "ready": True,
             "failed": False, "reason": "verification owner ready but idle"},
        ])
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, interval_s=15)
        result = asyncio.run(supervisor.check_active_task(force=True))
        self.assertEqual(result["escalation_state"], "DEFERRED_ACTIVE_TASK")
        self.assertFalse(fixture.engine.runtime_handoff_pending)
        self.assertIsNone(state.task_continuity())

    def test_capability_truth_is_read_freshly_for_each_boundary_check(self):
        state = AgentState()
        fixture = _Engine(state)
        bus = fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        first = fixture.engine.check_action_fence(fixture.action)
        bus.set_state("uia", "DEGRADED")
        second = fixture.engine.check_action_fence(fixture.action)
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(bus.calls, 2)

    def test_unaffected_capability_is_not_fabricated_as_requirement(self):
        state = AgentState()
        fixture = _Engine(state)
        fixture.action.action_type = "run_powershell"
        fixture.set_bus([
            _record("perception"), _record("computer_tools"),
            _record("uia", "DEGRADED"),
        ])
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertTrue(decision.allowed)
        self.assertNotIn("uia", decision.required_capabilities)

    def test_existing_authorities_remain_in_place(self):
        state = AgentState()
        fixture = _Engine(state)
        original_task = fixture.engine.task_state
        original_executor = fixture.engine.action_executor
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        fixture.engine.check_action_fence(fixture.action)
        self.assertIs(fixture.engine.task_state, original_task)
        self.assertIs(fixture.engine.action_executor, original_executor)
        self.assertIs(fixture.engine.verification, fixture.engine.verification)
        self.assertIsInstance(
            fixture.engine.runtime_handoff_pending, bool)

    def test_recovery_does_not_move_current_step_backward(self):
        state = AgentState()
        fixture = _Engine(state, step=2, completed=2, verified=True)
        original_step = fixture.task.current_step
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        fixture.engine.pause_for_runtime_health("uia", "degraded")
        evaluation = fixture.engine.evaluate_runtime_handoff(
            "uia", {"state": "READY", "ready": True},
            {"outcome": "RECOVERED"})
        fixture.engine.complete_runtime_handoff(evaluation)
        self.assertEqual(fixture.task.current_step, original_step)

    def test_recovery_release_still_blocks_unverified_current_action(self):
        state = AgentState()
        action_result = SimpleNamespace(action=None, success=True)
        fixture = _Engine(state, verified=False, action_result=action_result)
        fixture.task.last_action_result.action = fixture.action
        fixture.set_bus([
            _record("perception"), _record("computer_tools"), _record("uia"),
        ])
        fixture.engine.pause_for_runtime_health("uia", "degraded")
        fixture.engine.complete_runtime_handoff({
            "capability": "uia", "decision": "RESUME_SAFE",
            "safe_to_resume": True, "reason": "test release",
        })
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision, "BLOCK_VERIFICATION")


if __name__ == "__main__":
    unittest.main()
