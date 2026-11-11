"""Phase 20 idempotent side-effect recovery and user-handoff tests."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.agent_state import AgentState
from core.orchestrator.action_executor import ActionExecutionResult, ExecutionStatus
from core.orchestrator.state import (
    Action,
    GroundingMethod,
    OrchestratorState,
    PerceptionSnapshot,
    RecoveryDecision,
    RiskLevel,
    TargetSpec,
    VerificationMethod,
    VerificationResult,
)
from core.orchestrator.task_engine import TaskEngine


class _Bus:
    def __init__(self):
        self.records = {
            name: {"name": name, "state": "READY", "ready": True,
                   "failed": False, "reason": ""}
            for name in ("perception", "computer_tools", "uia")
        }
        self.calls = 0

    def capability_status(self, payload=None):
        self.calls += 1
        return {"success": True, "data": {"capabilities": [
            dict(record) for record in self.records.values()
        ]}}


class _Fixture:
    def __init__(self, state, action_type="click", phase=OrchestratorState.ACTING,
                 last_result=False):
        self.state = state
        self.executor = SimpleNamespace(execute=AsyncMock())
        self.verification = SimpleNamespace(verify=AsyncMock())
        self.recovery = SimpleNamespace(decide=AsyncMock())
        self.engine = TaskEngine(
            SimpleNamespace(), SimpleNamespace(), self.executor,
            self.verification, self.recovery, agent_state=state)
        task = self.engine.start_task("side-effect handoff test")
        task.subgoals = [SimpleNamespace(
            description="one controlled side effect", completed=False,
        )]
        task.current_step = 0
        task.current_application = "Notepad"
        task.current_window = {"app": "Notepad", "title": "Handoff.txt"}
        task.last_observation = PerceptionSnapshot(
            active_app="Notepad", active_window=task.current_window,
            age_seconds=0.0,
        )
        self.action = Action(
            task_id=task.task_id, step_id=0,
            intent="perform controlled side effect", action_type=action_type,
            target=TargetSpec(text_match="Save"), target_description="Save",
            grounding_method=GroundingMethod.UIA, risk_level=RiskLevel.LOW,
        )
        task.last_action = self.action
        task.last_action_result = (
            SimpleNamespace(
                action=self.action, success=True,
                subgoal=task.subgoals[0], verification=None,
            ) if last_result else None
        )
        task.verification_result = VerificationResult(
            success=False, method=VerificationMethod.CUSTOM,
            evidence="outcome not established",
        )
        task.side_effect_status = "UNKNOWN"
        self.engine._state = phase
        self.engine._running = True
        self.engine._observe_for_task = lambda *args, **kwargs: task.last_observation
        self.engine.set_capability_bus(_Bus())
        self.task = task


class IdempotentHandoffTests(unittest.TestCase):
    def test_side_effect_contract_distinguishes_replay_safety(self):
        click = Action("id", 0, "x", "click", TargetSpec(), "x")
        open_app = Action("id", 0, "x", "open_app", TargetSpec(), "x")
        read = Action("id", 0, "x", "read_file", TargetSpec(), "x")
        self.assertEqual(
            TaskEngine.side_effect_contract(click),
            {"side_effect_class": "NON_IDEMPOTENT", "replay_policy": "DO_NOT_RETRY"})
        self.assertEqual(
            TaskEngine.side_effect_contract(open_app),
            {"side_effect_class": "IDEMPOTENT", "replay_policy": "SAFE_RETRY"})
        self.assertEqual(
            TaskEngine.side_effect_contract(read),
            {"side_effect_class": "READ_ONLY", "replay_policy": "SAFE_RETRY"})

    def test_action_start_marks_side_effect_in_flight(self):
        state = AgentState()
        fixture = _Fixture(state)
        fixture.executor.execute.return_value = ActionExecutionResult(
            success=True, status=ExecutionStatus.EXECUTED,
            message="controlled", error="", duration_seconds=0.0,
            actual_result=None,
        )
        asyncio.run(fixture.engine._do_acting())
        self.assertEqual(fixture.task.side_effect_status,
                         "EXECUTED_UNVERIFIED")
        self.assertEqual(fixture.engine.state, OrchestratorState.VERIFYING)
        fixture.executor.execute.assert_awaited_once()

    def test_existing_verification_marks_side_effect_verified(self):
        state = AgentState()
        fixture = _Fixture(state)
        fixture.executor.execute.return_value = ActionExecutionResult(
            success=True, status=ExecutionStatus.EXECUTED,
            message="controlled", error="", duration_seconds=0.0,
            actual_result=None,
        )
        asyncio.run(fixture.engine._do_acting())
        fixture.verification.verify.return_value = VerificationResult(
            success=True, method=VerificationMethod.CUSTOM,
            evidence="existing verification passed", confidence=0.9)
        asyncio.run(fixture.engine._do_verifying())
        self.assertEqual(fixture.task.side_effect_status, "VERIFIED")
        self.assertEqual(len(fixture.task.completed_steps), 1)

    def test_non_idempotent_unknown_outcome_handoffs_without_retry(self):
        state = AgentState()
        fixture = _Fixture(state, phase=OrchestratorState.RECOVERING,
                           last_result=True)
        notifications = []
        fixture.engine.handoff_notifier = notifications.append
        asyncio.run(fixture.engine._do_recovering())
        self.assertEqual(fixture.task.side_effect_status, "HANDOFF_REQUIRED")
        self.assertEqual(fixture.task.current_step, 0)
        self.assertEqual(fixture.engine.state, OrchestratorState.RECOVERING)
        self.assertEqual(fixture.executor.execute.await_count, 0)
        fixture.recovery.decide.assert_not_awaited()
        self.assertEqual(state.task_handoff()["state"], "PENDING_USER")
        self.assertEqual(len(notifications), 1)

        # Repeating the same handoff does not create another notification.
        fixture.engine.handoff_to_user(
            fixture.task.side_effect_reason, fixture.action)
        self.assertEqual(len(notifications), 1)
        self.assertFalse(hasattr(state, "_task_handoff_history"))

    def test_idempotent_action_uses_existing_bounded_retry_path(self):
        state = AgentState()
        fixture = _Fixture(state, action_type="open_app",
                           phase=OrchestratorState.RECOVERING, last_result=True)
        fixture.recovery.decide.return_value = RecoveryDecision(
            should_retry=True, reason="safe retry available",
            action="retry_same",
        )
        asyncio.run(fixture.engine._do_recovering())
        self.assertEqual(fixture.task.side_effect_status, "RETRY_ALLOWED")
        self.assertEqual(fixture.engine.state, OrchestratorState.OBSERVING)
        fixture.recovery.decide.assert_awaited_once()

    def test_action_fence_allows_only_marked_safe_retry(self):
        state = AgentState()
        fixture = _Fixture(state, action_type="open_app", last_result=True)
        fixture.task.side_effect_status = "RETRY_ALLOWED"
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.verification_basis, "first action; no prior step required")

    def test_unknown_non_idempotent_action_remains_blocked_after_handoff(self):
        state = AgentState()
        fixture = _Fixture(state)
        fixture.engine.handoff_to_user("ambiguous side effect", fixture.action)
        decision = fixture.engine.check_action_fence(fixture.action)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision, "HANDOFF_REQUIRED")
        fixture.executor.execute.assert_not_awaited()

    def test_user_resolution_allows_at_most_one_explicit_retry(self):
        state = AgentState()
        fixture = _Fixture(state)
        fixture.engine.handoff_to_user("review this side effect", fixture.action)
        first = fixture.engine.resolve_user_handoff("retry_once")
        self.assertTrue(first["retry_authorized"])
        self.assertEqual(fixture.task.side_effect_retry_count, 1)
        self.assertEqual(fixture.task.side_effect_status, "RETRY_ALLOWED")
        second = fixture.engine.resolve_user_handoff("retry_once")
        self.assertFalse(second["retry_authorized"])
        self.assertEqual(second["state"], "STOPPED")
        self.assertEqual(fixture.task.side_effect_status, "HANDOFF_STOPPED")
        self.assertEqual(state.task_handoff()["state"], "STOPPED")

    def test_handoff_preserves_step_and_does_not_create_history(self):
        state = AgentState()
        fixture = _Fixture(state)
        before_step = fixture.task.current_step
        fixture.engine.handoff_to_user("review before replay", fixture.action)
        self.assertEqual(fixture.task.current_step, before_step)
        self.assertEqual(state.task_handoff()["step"],
                         "one controlled side effect")
        self.assertFalse(hasattr(state, "_task_handoff_history"))
        self.assertFalse(hasattr(fixture.engine, "_handoff_history"))


if __name__ == "__main__":
    unittest.main()
