"""Phase 20 reliability-aware task continuity and recovery handoff tests."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.agent_state import AgentState
from core.capability_bus import CapabilityBus
from core.orchestrator.state import OrchestratorState
from core.orchestrator.task_engine import TaskEngine
from core.runtime_health import RuntimeHealthSupervisor


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class _Thread:
    def is_alive(self):
        return True


class _ScreenMap:
    def __init__(self, rows=None, recover=True):
        self.rows = list(rows if rows is not None else [{"name": "ready"}])
        self.recover = recover
        self.refresh_calls = 0
        self._thread = _Thread()

    def available(self):
        return True

    def current_view(self, limit=10):
        return "ready"

    def refresh_sync(self):
        self.refresh_calls += 1
        if self.recover:
            self.rows = [{"name": "recovered"}]
        return self.recover


class _OnlyBus:
    def __init__(self, inner, names):
        self.inner = inner
        self.names = set(names)

    def capability_status(self, payload=None):
        response = self.inner.capability_status(payload or {})
        if response.get("success"):
            response = dict(response)
            data = dict(response.get("data") or {})
            data["capabilities"] = [
                record for record in (data.get("capabilities") or [])
                if record.get("name") in self.names
            ]
            response["data"] = data
        return response

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _StatusBus:
    def __init__(self, records):
        self.records = list(records)
        self.calls = 0

    def capability_status(self, payload=None):
        self.calls += 1
        return {
            "success": True,
            "data": {"capabilities": [dict(record) for record in self.records]},
        }


class _TaskEngineFixture:
    def __init__(self, state, phase=OrchestratorState.OBSERVING,
                 verified=True, action_success=True):
        self.engine = TaskEngine(
            Mock(), Mock(), Mock(), Mock(), Mock(), agent_state=state)
        task = self.engine.start_task("finish the current report")
        task.subgoals = [SimpleNamespace(
            description="Continue at the verified continuation point",
            verification_method=SimpleNamespace(value="CUSTOM"),
        )]
        task.current_application = "Notepad"
        task.current_window = {
            "app": "Notepad", "title": "Report.txt", "hwnd": 42,
            "focused_name": "Editor",
        }
        task.last_action_result = SimpleNamespace(
            success=action_success,
        )
        task.verification_result = SimpleNamespace(
            success=verified,
            method="CUSTOM",
            evidence="existing task verification passed"
            if verified else "verification did not establish the outcome",
        )
        self.engine._state = phase
        self.engine._running = True
        self.task = task


class TaskContinuityTests(unittest.TestCase):
    def test_active_task_context_is_preserved_during_degradation(self):
        clock = _Clock()
        state = AgentState(clock=clock)
        fixture = _TaskEngineFixture(state)
        tree = _ScreenMap(rows=[])
        bus = _OnlyBus(CapabilityBus(), {"uia"})
        bus.inner.attach(screentree=tree)
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, clock=clock, interval_s=15)

        result = asyncio.run(supervisor.check_active_task(force=True))
        continuity = state.task_continuity()
        self.assertEqual(result["escalation_state"], "DEFERRED_ACTIVE_TASK")
        self.assertEqual(continuity["goal"], "finish the current report")
        self.assertEqual(continuity["step_index"], 0)
        self.assertEqual(continuity["step_total"], 1)
        self.assertEqual(continuity["application"], "Notepad")
        self.assertEqual(continuity["window"]["title"], "Report.txt")
        self.assertEqual(continuity["capability"], "uia")
        self.assertEqual(continuity["resume_decision"], "HANDOFF_REQUIRED")

    def test_unaffected_active_task_is_not_interrupted(self):
        clock = _Clock()
        state = AgentState(clock=clock)
        fixture = _TaskEngineFixture(state)
        bus = _StatusBus([{
            "name": "stt", "state": "DEGRADED", "ready": False,
            "reason": "voice worker stalled",
        }])
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, clock=clock, interval_s=15)

        result = asyncio.run(supervisor.check_active_task(force=True))
        self.assertEqual(result["escalation_state"], "DEFERRED_ACTIVE_TASK")
        self.assertFalse(fixture.engine.runtime_handoff_pending)
        self.assertFalse(fixture.engine._paused)
        self.assertIsNone(state.task_continuity())
        self.assertEqual(bus.calls, 1)

    def test_existing_supervisor_recovery_owns_recovery_and_handoff_resumes(self):
        clock = _Clock()
        state = AgentState(clock=clock)
        fixture = _TaskEngineFixture(state)
        tree = _ScreenMap(rows=[])
        bus = _OnlyBus(CapabilityBus(), {"uia"})
        bus.inner.attach(screentree=tree)
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, clock=clock, interval_s=15)

        asyncio.run(supervisor.check_active_task(force=True))
        clock.advance(16)
        result = asyncio.run(supervisor.check_active_task())
        continuity = state.task_continuity()
        self.assertEqual(tree.refresh_calls, 1)
        self.assertEqual(result["recovery_result"][0]["outcome"], "RECOVERED")
        self.assertEqual(result["task_continuity"]["handoff_state"], "RESUMED")
        self.assertEqual(result["task_continuity"]["decision"], "RESUME_SAFE")
        self.assertTrue(result["task_continuity"]["safe_to_resume"])
        self.assertEqual(continuity["interruption_state"], "RESUME_SAFE")
        self.assertEqual(fixture.task.current_step, 0)
        self.assertFalse(fixture.engine.runtime_handoff_pending)
        self.assertFalse(fixture.engine._paused)

    def test_capability_recovery_does_not_imply_task_safe_resume(self):
        clock = _Clock()
        state = AgentState(clock=clock)
        fixture = _TaskEngineFixture(
            state, phase=OrchestratorState.ACTING, verified=False,
            action_success=False)
        tree = _ScreenMap(rows=[])
        bus = _OnlyBus(CapabilityBus(), {"uia"})
        bus.inner.attach(screentree=tree)
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, clock=clock, interval_s=15)

        asyncio.run(supervisor.check_active_task(force=True))
        clock.advance(16)
        result = asyncio.run(supervisor.check_active_task())
        self.assertEqual(result["recovery_result"][0]["outcome"], "RECOVERED")
        self.assertEqual(result["task_continuity"]["decision"], "RESUME_UNSAFE")
        self.assertFalse(result["task_continuity"]["safe_to_resume"])
        self.assertTrue(fixture.engine.runtime_handoff_pending)
        self.assertTrue(fixture.engine._paused)
        self.assertTrue(result["task_interruption"])

    def test_partial_execution_produces_resume_unsafe(self):
        state = AgentState()
        fixture = _TaskEngineFixture(
            state, phase=OrchestratorState.ACTING, verified=False,
            action_success=True)
        evaluation = fixture.engine.evaluate_runtime_handoff(
            "uia", {"state": "READY", "ready": True},
            {"outcome": "RECOVERED"})
        self.assertEqual(evaluation["decision"], "RESUME_UNSAFE")
        self.assertEqual(evaluation["verification_result"]["action_status"],
                         "EXECUTED_UNVERIFIED")

    def test_failed_recovery_preserves_context_and_escalates(self):
        clock = _Clock()
        state = AgentState(clock=clock)
        fixture = _TaskEngineFixture(state)
        tree = _ScreenMap(rows=[], recover=False)
        bus = _OnlyBus(CapabilityBus(), {"uia"})
        bus.inner.attach(screentree=tree)
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, clock=clock, interval_s=15)

        asyncio.run(supervisor.check_active_task(force=True))
        clock.advance(16)
        result = asyncio.run(supervisor.check_active_task())
        continuity = state.task_continuity()
        self.assertEqual(result["recovery_result"][0]["outcome"], "DEGRADED")
        self.assertEqual(result["task_continuity"]["decision"], "ESCALATE")
        self.assertEqual(continuity["goal"], "finish the current report")
        self.assertEqual(continuity["capability"], "uia")
        self.assertTrue(fixture.engine.runtime_handoff_pending)
        self.assertEqual(tree.refresh_calls, 2)

    def test_repeated_health_checkpoints_do_not_duplicate_handoff_state(self):
        state = AgentState()
        fixture = _TaskEngineFixture(state)
        first = fixture.engine.pause_for_runtime_health("uia", "degraded")
        second = fixture.engine.pause_for_runtime_health("uia", "degraded")
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(first["captured_at"], second["captured_at"])
        self.assertEqual(state.task_continuity()["task_id"],
                         fixture.task.task_id)
        self.assertFalse(hasattr(state, "_continuity_history"))

    def test_bounded_continuity_state_does_not_grow(self):
        state = AgentState()
        fixture = _TaskEngineFixture(state)
        for index in range(200):
            fixture.engine.pause_for_runtime_health(
                "uia", "runtime capability degraded %d" % index)
        continuity = state.task_continuity()
        self.assertEqual(continuity["task_id"], fixture.task.task_id)
        self.assertEqual(continuity["capability"], "uia")
        self.assertEqual(len(continuity["window"]), 4)
        self.assertFalse(hasattr(state, "_continuity_history"))

    def test_task_engine_state_is_not_replaced_by_continuity(self):
        state = AgentState()
        fixture = _TaskEngineFixture(state)
        original_task = fixture.engine.task_state
        original_step = fixture.task.current_step
        fixture.engine.pause_for_runtime_health("uia", "degraded")
        self.assertIs(fixture.engine.task_state, original_task)
        self.assertEqual(fixture.task.current_step, original_step)
        self.assertEqual(state.snapshot()["task_continuity"]["task_id"],
                         original_task.task_id)

    def test_recovery_does_not_replay_verified_steps(self):
        clock = _Clock()
        state = AgentState(clock=clock)
        fixture = _TaskEngineFixture(state)
        original_completed = list(fixture.task.completed_steps)
        tree = _ScreenMap(rows=[])
        bus = _OnlyBus(CapabilityBus(), {"uia"})
        bus.inner.attach(screentree=tree)
        supervisor = RuntimeHealthSupervisor(
            bus, fixture.engine, state, clock=clock, interval_s=15)
        asyncio.run(supervisor.check_active_task(force=True))
        clock.advance(16)
        asyncio.run(supervisor.check_active_task())
        self.assertEqual(fixture.task.current_step, 0)
        self.assertEqual(fixture.task.completed_steps, original_completed)
        self.assertEqual(tree.refresh_calls, 1)

    def test_existing_resume_loop_waits_then_continues_from_same_step(self):
        state = AgentState()
        fixture = _TaskEngineFixture(state)
        calls = []

        async def fake_step():
            calls.append(fixture.task.current_step)
            fixture.engine._running = False

        fixture.engine._step = fake_step
        fixture.engine.pause_for_runtime_health("uia", "degraded")

        async def scenario():
            runner = asyncio.create_task(fixture.engine.resume())
            await asyncio.sleep(0.01)
            self.assertFalse(runner.done())
            evaluation = fixture.engine.evaluate_runtime_handoff(
                "uia", {"state": "READY", "ready": True},
                {"outcome": "RECOVERED"})
            fixture.engine.complete_runtime_handoff(evaluation)
            await runner

        asyncio.run(scenario())
        self.assertEqual(calls, [0])
        self.assertEqual(fixture.task.current_step, 0)
        self.assertFalse(fixture.engine.runtime_handoff_pending)

    def test_existing_reliability_snapshot_remains_available(self):
        state = AgentState()
        fixture = _TaskEngineFixture(state)
        state.record_health_checkpoint({
            "checked_at": 100.0,
            "completed_at": 100.0,
            "capabilities_checked": ["uia"],
            "changed_capabilities": [],
            "severity": "HEALTHY",
            "escalation_state": "NONE",
            "recovery_result": [],
        }, now=100.0)
        fixture.engine.pause_for_runtime_health("uia", "degraded")
        snapshot = state.snapshot()
        self.assertEqual(snapshot["reliability"]["health_checks_completed"], 1)
        self.assertEqual(snapshot["task_continuity"]["capability"], "uia")


if __name__ == "__main__":
    unittest.main()
