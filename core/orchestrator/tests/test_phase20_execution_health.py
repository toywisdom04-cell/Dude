"""Phase 20 canonical execution-health regression tests."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import core.tools as tools
from core.orchestrator.goal_executor import CognitiveMode, CognitiveInterpretation
from core.orchestrator.intelligence_router import IntelligenceRouter, RouteDecision
from core.orchestrator.procedure_store import ProcedureStore
from core.orchestrator.realtime_voice import wire_realtime_voice
from core.orchestrator.state import (
    MemoryBundle,
    OrchestratorState,
    PerceptionLevel,
    PerceptionSnapshot,
    Plan,
    Procedure,
    StepResult,
    SubGoal,
    TaskState,
)
from core.orchestrator.task_engine import TaskEngine
from platform_utils.windows import WindowsAdapter


class _Model:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            content="1. screenshot | desktop | evidence captured"
        )


class _ContextProcedureStore:
    def __init__(self, procedure):
        self.procedure = procedure
        self.context = None

    def find_by_goal(self, *args):
        return []

    def find_by_normalized_goal(self, *args):
        return []

    def find_by_context(self, context, *args):
        self.context = context
        return [self.procedure]

    def has_recent_failures(self, *args):
        return False


class _Voice:
    def __init__(self):
        self.cfg = SimpleNamespace(
            get=lambda *args, **kwargs: kwargs.get("default", False))
        self.silent = False

    def say(self, *args, **kwargs):
        return None

    @property
    def speaking(self):
        return False


class _PrebuiltEngine:
    def __init__(self):
        self._state = OrchestratorState.IDLE
        self._running = False
        self._task_state = None
        self.transitions = []

    def start_task(self, goal, *args, **kwargs):
        self._task_state = TaskState(goal=goal)
        self._running = True
        self._state = OrchestratorState.LISTENING
        return self._task_state

    def handle_user_input(self, text):
        self._task_state.user_interrupt = text
        self._state = OrchestratorState.UNDERSTANDING

    def _transition(self, state):
        self._state = state
        self.transitions.append(state)

    async def resume(self):
        if not self._running:
            return self._task_state
        step = self._task_state.subgoals[0]
        self._task_state.completed_steps = [
            StepResult(subgoal=step, success=True)
        ]
        self._running = False
        self._state = OrchestratorState.DONE
        return self._task_state


class _PlanFrontDoor:
    def process_turn(self, **kwargs):
        return CognitiveInterpretation(
            intent_kind=CognitiveMode.COMPUTER_WORK,
            user_goal="take a screenshot",
            desired_outcome="capture the screen",
            requires_computer_action=True,
            execution_plan=Plan(
                task_id="prebuilt",
                objective="take a screenshot",
                steps=[SubGoal(
                    description="capture the screen",
                    intent="take a screenshot",
                    action_type="screenshot",
                    target_description="desktop",
                )],
            ),
        )


class TestModelDecompositionHealth(unittest.TestCase):
    def test_task_engine_uses_the_existing_generate_request_contract(self):
        model = _Model()
        engine = TaskEngine(model, Mock(), Mock(), Mock(), Mock())
        steps = asyncio.run(engine._decompose_via_model(
            "capture the screen", "capture the screen"))
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].action_type, "screenshot")
        self.assertEqual(len(model.requests), 1)
        self.assertTrue(hasattr(model.requests[0], "prompt"))


class TestPrebuiltExecutionHealth(unittest.TestCase):
    def test_prebuilt_plan_enters_task_engine_running_state(self):
        engine = _PrebuiltEngine()
        controller = wire_realtime_voice(
            voice=_Voice(),
            ear=Mock(),
            memory=Mock(),
            brain=Mock(),
            task_engine=engine,
            cognitive_front_door=_PlanFrontDoor(),
        )

        result = controller.deep_handler("take a screenshot", {"fast": False})

        self.assertEqual(result.speech_reply, "Done. Completed 1 steps")
        self.assertEqual(engine._state, OrchestratorState.DONE)
        self.assertIn(OrchestratorState.OBSERVING, engine.transitions)
        self.assertEqual(len(engine._task_state.completed_steps), 1)


class TestComputerToolHealth(unittest.TestCase):
    def test_windows_adapter_contract_opens_url_with_os_handler(self):
        adapter = WindowsAdapter()
        with patch("platform_utils.windows.os.startfile") as startfile:
            self.assertTrue(adapter.open_url("https://example.test"))
        startfile.assert_called_once_with("https://example.test")

    def test_registered_open_url_tool_reaches_adapter(self):
        adapter = WindowsAdapter()
        with patch.object(tools, "PLATFORM", adapter), \
                patch("platform_utils.windows.os.startfile") as startfile:
            result = tools.open_url(
                None, {"url": "example.test"})
        self.assertEqual(result, "Opened https://example.test")
        startfile.assert_called_once_with("https://example.test")


class TestProcedureBoundaryHealth(unittest.TestCase):
    def test_front_door_goal_mapping_is_normalized_before_routing(self):
        procedure = Procedure(
            goal="Backup project files",
            goal_type="automate",
            confidence=0.9,
            steps=[{
                "description": "archive project",
                "action_type": "execute",
                "target": "project",
            }],
        )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db:
            db_path = db.name
        try:
            store = ProcedureStore(db_path)
            store.save(procedure)
            router = IntelligenceRouter(procedure_store=store)
            result = router.route(
                {"task_id": "turn_1", "goal": "Backup project files"},
                PerceptionSnapshot(
                    capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW),
                "Backup project files",
            )
            self.assertEqual(result.decision, RouteDecision.VERIFIED_PROCEDURE)
            self.assertEqual(result.plan.steps[0].action_type, "execute")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_legacy_string_fact_shape_reaches_context_lookup(self):
        procedure = Procedure(
            goal="Context procedure",
            goal_type="automate",
            confidence=0.9,
            steps=[{
                "description": "use context",
                "action_type": "execute",
                "target": "context",
            }],
        )
        store = _ContextProcedureStore(procedure)
        router = IntelligenceRouter(procedure_store=store)
        state = TaskState(
            goal="use remembered context",
            relevant_memory=MemoryBundle(facts=["remembered context"]),
        )
        result = router.route(
            state,
            PerceptionSnapshot(
                capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW),
            "use remembered context",
        )
        self.assertEqual(result.decision, RouteDecision.VERIFIED_PROCEDURE)
        self.assertEqual(store.context, {"facts": ["remembered context"]})


if __name__ == "__main__":
    unittest.main()
