"""Phase 20 permission decision and confirmation handoff tests."""
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
    PermissionState,
    RiskLevel,
    SubGoal,
    TargetSpec,
    VerificationMethod,
)
from core.orchestrator.task_engine import TaskEngine


class _Gate:
    def __init__(self, answer=True, requires=True, has_ask=True):
        self.answer = answer
        self.requires = requires
        self.questions = []
        if not has_ask:
            self.ask = None

    def requires_permission(self, action_type):
        self.questions.append(("requires", action_type))
        return self.requires

    def ask(self, question):
        self.questions.append(("ask", question))
        return self.answer


class PermissionHandoffTests(unittest.TestCase):
    def _engine(self, gate):
        state = AgentState()
        executor = SimpleNamespace(execute=AsyncMock())
        engine = TaskEngine(
            Mock(), Mock(), executor, Mock(), Mock(),
            permission_gate=gate, agent_state=state)
        task = engine.start_task("permission test")
        task.subgoals = [SubGoal(
            description="delete a harmless test artifact",
            action_type="delete_path",
            target_description="test-artifact.txt",
        )]
        task.current_step = 0
        action = Action(
            task_id=task.task_id, step_id=0, intent="delete test artifact",
            action_type="delete_path", target=TargetSpec(text_match="test-artifact.txt"),
            target_description="test-artifact.txt",
            grounding_method=GroundingMethod.ABSOLUTE_COORDS,
            verification_method=VerificationMethod.FILE_EXISTS,
            risk_level=RiskLevel.HIGH,
        )
        task.last_action = action
        engine._state = OrchestratorState.PLANNING
        engine._running = True
        engine._plan_action = AsyncMock(return_value=action)
        return engine, task, executor

    def test_pending_permission_uses_existing_confirmation_gate(self):
        gate = _Gate(answer=True)
        engine, task, executor = self._engine(gate)
        asyncio.run(engine._do_planning())
        self.assertEqual(task.permission_state, PermissionState.GRANTED)
        self.assertEqual(engine.state, OrchestratorState.ACTING)
        self.assertTrue(any(kind == "ask" for kind, _ in gate.questions))
        executor.execute.assert_not_awaited()

    def test_denied_permission_fails_before_action_execution(self):
        gate = _Gate(answer=False)
        engine, task, executor = self._engine(gate)
        asyncio.run(engine._do_planning())
        self.assertEqual(task.permission_state, PermissionState.DENIED)
        self.assertEqual(engine.state, OrchestratorState.FAILED)
        self.assertIn("permission denied", task.failure_reason)
        executor.execute.assert_not_awaited()

    def test_missing_confirmation_capability_fails_closed(self):
        gate = _Gate(answer=True, has_ask=False)
        engine, task, executor = self._engine(gate)
        asyncio.run(engine._do_planning())
        self.assertEqual(task.permission_state, PermissionState.DENIED)
        self.assertEqual(engine.state, OrchestratorState.FAILED)
        executor.execute.assert_not_awaited()

    def test_non_permissioned_action_remains_not_required(self):
        gate = _Gate(answer=False, requires=False)
        engine, task, executor = self._engine(gate)
        asyncio.run(engine._do_planning())
        self.assertEqual(task.permission_state, PermissionState.GRANTED)
        self.assertEqual(engine.state, OrchestratorState.ACTING)
        self.assertFalse(any(kind == "ask" for kind, _ in gate.questions))

    def test_async_confirmation_gate_is_awaited(self):
        gate = _Gate(answer=False)
        gate.ask = AsyncMock(return_value=True)
        engine, task, executor = self._engine(gate)
        asyncio.run(engine._do_planning())
        gate.ask.assert_awaited_once()
        self.assertEqual(task.permission_state, PermissionState.GRANTED)
        self.assertEqual(engine.state, OrchestratorState.ACTING)


if __name__ == "__main__":
    unittest.main()
