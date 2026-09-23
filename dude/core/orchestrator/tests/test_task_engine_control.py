"""Headless unit tests for TaskEngine control plane (Phase 5, P12/P13/P9).

Cancellation, pause/resume loop mechanics, task flags, and the metrics
summary. No desktop, no models; async parts use strict mocks.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from core.orchestrator.state import OrchestratorState, TaskState, TaskType
from core.orchestrator.task_engine import TaskEngine, summarize_metrics


def _engine():
    return TaskEngine(
        intelligence=Mock(),
        perception=Mock(),
        action_executor=Mock(),
        verification=Mock(),
        recovery=Mock(),
        memory=None,
    )


class TestTaskFlags(unittest.TestCase):
    def test_start_task_applies_flags(self):
        eng = _engine()
        ts = eng.start_task("goal", TaskType.AUTOMATE,
                            prefer_fresh_windows=True,
                            execution_mode="background")
        self.assertTrue(ts.prefer_fresh_windows)
        self.assertEqual(ts.execution_mode, "background")
        self.assertFalse(ts.cancel_requested)
        self.assertEqual(ts.owned_hwnds, set())

    def test_start_task_defaults(self):
        eng = _engine()
        ts = eng.start_task("goal")
        self.assertFalse(ts.prefer_fresh_windows)
        self.assertEqual(ts.execution_mode, "foreground")

    def test_request_cancel_sets_flag(self):
        eng = _engine()
        eng.start_task("goal")
        self.assertFalse(eng.task_state.cancel_requested)
        eng.request_cancel()
        self.assertTrue(eng.task_state.cancel_requested)


class TestCancelResume(unittest.TestCase):
    def test_cancel_stops_before_any_step(self):
        eng = _engine()
        eng._task_state = TaskState(goal="g")
        eng._state = OrchestratorState.OBSERVING
        eng._running = True
        eng._step = AsyncMock()
        eng.request_cancel()
        asyncio.run(eng.resume())
        eng._step.assert_not_called()
        self.assertTrue(eng.task_state.cancelled)
        self.assertEqual(eng.task_state.failure_reason,
                         "cancelled by user")
        self.assertEqual(eng.state, OrchestratorState.IDLE)
        self.assertFalse(eng._running)
        phases = [e.get("phase")
                  for e in eng.task_state.recovery_history]
        self.assertIn("cancelled", phases)

    def test_pause_stops_loop_with_state_preserved(self):
        eng = _engine()
        eng._task_state = TaskState(goal="g")
        eng._state = OrchestratorState.OBSERVING
        eng._running = True
        calls = [0]

        async def counting_step():
            calls[0] += 1
            if calls[0] >= 2:
                eng.pause()

        eng._step = counting_step
        asyncio.run(eng.resume())
        self.assertEqual(calls[0], 2)
        # Paused, not terminal: still running, still mid-task.
        self.assertTrue(eng._running)
        self.assertEqual(eng.state, OrchestratorState.OBSERVING)
        self.assertFalse(eng.task_state.cancelled)


class TestSummarizeMetrics(unittest.TestCase):
    def _state(self):
        ts = TaskState(goal="g")
        ts.metrics = {
            "seconds_in_understanding": 1.0,
            "seconds_in_planning": 2.0,
            "seconds_in_observing": 4.0,
            "steps": 10,
            "actions": 6,
            "action_failures": 1,
            "recoveries": 2,
            "verification_failures": 1,
        }
        ts.window_decisions = [
            {"decision": "open_new"},
            {"decision": "reuse_existing"},
            {"decision": "reuse_existing"},
            {"decision": "not_found"},
        ]
        ts.recovery_history = [
            {"phase": "dialog_classification"},
            {"phase": "other"},
        ]
        ts.subgoals = [Mock(), Mock(), Mock()]
        ts.current_step = 3
        return ts

    def test_summary_totals(self):
        s = summarize_metrics(self._state())
        self.assertAlmostEqual(s["total_duration_seconds"], 7.0)
        self.assertAlmostEqual(s["planning_seconds"], 3.0)
        self.assertAlmostEqual(s["perception_seconds"], 4.0)
        self.assertEqual(s["steps"], 10)
        self.assertEqual(s["actions"], 6)
        self.assertEqual(s["action_failures"], 1)
        self.assertEqual(s["retries"], 2)
        self.assertEqual(s["recovery_count"], 2)
        self.assertEqual(s["window_launches"], 2)  # open_new + not_found
        self.assertEqual(s["window_reuses"], 2)
        self.assertEqual(s["verification_failures"], 1)
        self.assertEqual(s["dialog_events"], 1)
        self.assertTrue(s["final_success"])

    def test_summary_incomplete_task(self):
        ts = self._state()
        ts.current_step = 1
        s = summarize_metrics(ts)
        self.assertFalse(s["final_success"])

    def test_summary_empty_state(self):
        s = summarize_metrics(TaskState(goal="g"))
        self.assertEqual(s["actions"], 0)
        self.assertFalse(s["final_success"])


if __name__ == '__main__':
    unittest.main()
