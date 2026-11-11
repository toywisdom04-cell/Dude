"""Phase 20 continuous health supervision and bounded escalation tests."""
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
        self.now += seconds


class _Thread:
    def __init__(self, alive=True):
        self._alive = alive

    def is_alive(self):
        return self._alive


class _Proc:
    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 1


class _Ear:
    def __init__(self, healthy=True, results=None):
        self._running = True
        self._mic_thread = _Thread(True)
        self._stt_thread = _Thread(True)
        self._stt_proc = _Proc(healthy)
        self.results = list(results or [])
        self.calls = 0

    def warm_start(self):
        self.calls += 1
        result = self.results.pop(0) if self.results else True
        if result:
            self._stt_proc = _Proc(True)
        return result


class _ScreenMap:
    def __init__(self, rows=None):
        self._thread = _Thread(True)
        self.rows = list(rows or [{"name": "ready"}])
        self.refresh_calls = 0

    def available(self):
        return True

    def current_view(self, limit=10):
        return "ready"

    def refresh_sync(self):
        self.refresh_calls += 1
        self.rows = [{"name": "recovered"}]
        return True


class _Hand:
    def __init__(self, state="AVAILABLE", reason=""):
        self.state = state
        self.reason = reason

    def status(self):
        return {"state": self.state, "reason": self.reason}


class _CountingBus:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def capability_status(self, payload=None):
        self.calls += 1
        return self.inner.capability_status(payload or {})

    def __getattr__(self, name):
        return getattr(self.inner, name)


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


class _FailingBus:
    def capability_status(self, payload=None):
        return {"success": False, "error": "status backend unavailable"}


class HealthSupervisionTests(unittest.TestCase):
    def _engine(self, state=None):
        return TaskEngine(
            Mock(), Mock(), Mock(), Mock(), Mock(),
            agent_state=state or AgentState(),
        )

    def test_healthy_unchanged_state_is_a_cheap_noop(self):
        clock = _Clock()
        bus = _CountingBus(_OnlyBus(CapabilityBus(), {"stt"}))
        bus.inner.inner.attach(ear=_Ear(healthy=True))
        supervisor = RuntimeHealthSupervisor(
            bus, self._engine(), clock=clock, interval_s=15)
        first = asyncio.run(supervisor.check())
        second = asyncio.run(supervisor.check())
        self.assertEqual(first["severity"], "HEALTHY")
        self.assertEqual(first["changed_capabilities"], [])
        self.assertEqual(second["escalation_state"], "NOOP_CADENCE")
        self.assertEqual(second["capabilities_checked"], [])
        self.assertEqual(bus.calls, 1)

    def test_ready_to_degraded_uia_transition_repairs_and_clears(self):
        clock = _Clock()
        tree = _ScreenMap()
        bus = _CountingBus(_OnlyBus(CapabilityBus(), {"uia"}))
        bus.inner.inner.attach(screentree=tree)
        state = AgentState()
        supervisor = RuntimeHealthSupervisor(
            bus, self._engine(state), clock=clock, interval_s=15)
        asyncio.run(supervisor.check())
        tree.rows = []
        clock.advance(16)
        result = asyncio.run(supervisor.check())
        self.assertEqual(result["changed_capabilities"][0]["to"], "DEGRADED")
        self.assertTrue(result["recovery_attempted"])
        self.assertEqual(result["recovery_result"][0]["outcome"], "RECOVERED")
        self.assertEqual(result["post_repair_status"]["state"], "READY")
        self.assertEqual(result["escalation_state"], "CLEARED")
        self.assertTrue(result["verification_result"]["success"])
        self.assertEqual(tree.refresh_calls, 1)
        self.assertIn("uia: RECOVERED", state.snapshot()["recovery"])

    def test_failed_recovery_escalates_then_deduplicates_identical_failure(self):
        clock = _Clock()
        ear = _Ear(healthy=True, results=[False, False, True])
        bus = _CountingBus(CapabilityBus())
        bus.inner.attach(ear=ear)
        supervisor = RuntimeHealthSupervisor(
            bus, self._engine(), clock=clock, interval_s=15)
        asyncio.run(supervisor.check())
        ear._stt_proc = _Proc(False)
        clock.advance(16)
        failed = asyncio.run(supervisor.check())
        self.assertEqual(failed["severity"], "FAILED")
        self.assertEqual(failed["escalation_state"], "ESCALATED")
        self.assertEqual(failed["recovery_result"][0]["attempts"], 2)
        self.assertEqual(ear.calls, 2)
        clock.advance(16)
        repeated = asyncio.run(supervisor.check())
        self.assertEqual(repeated["escalation_state"], "SUPPRESSED")
        self.assertFalse(repeated["recovery_attempted"])
        self.assertEqual(ear.calls, 2)

    def test_non_repairable_transition_escalates_without_recovery(self):
        clock = _Clock()
        hand = _Hand("AVAILABLE")
        bus = _CountingBus(CapabilityBus())
        bus.inner.attach(hand_tracker=hand)
        supervisor = RuntimeHealthSupervisor(
            bus, self._engine(), clock=clock, interval_s=15)
        asyncio.run(supervisor.check())
        hand.state = "UNAVAILABLE"
        hand.reason = "hardware disappeared"
        clock.advance(16)
        result = asyncio.run(supervisor.check())
        self.assertEqual(result["severity"], "ESCALATED")
        self.assertEqual(result["escalation_state"], "ESCALATED")
        self.assertFalse(result["recovery_attempted"])
        self.assertEqual(result["recovery_result"][0]["outcome"], "UNREPAIRED")

    def test_active_task_defers_supervision_without_touching_task_engine(self):
        clock = _Clock()
        engine = SimpleNamespace(
            state=OrchestratorState.ACTING,
            _running=True,
            recover_capability=Mock(side_effect=AssertionError("must not run")),
        )
        bus = _CountingBus(CapabilityBus())
        bus.inner.attach(ear=_Ear(healthy=True))
        supervisor = RuntimeHealthSupervisor(
            bus, engine, clock=clock, interval_s=15)
        result = asyncio.run(supervisor.check())
        self.assertEqual(result["escalation_state"], "DEFERRED_ACTIVE_TASK")
        self.assertEqual(result["capabilities_checked"], [])
        self.assertEqual(bus.calls, 0)
        engine.recover_capability.assert_not_called()

    def test_status_failure_reports_uncertainty(self):
        supervisor = RuntimeHealthSupervisor(
            _FailingBus(), self._engine(), interval_s=15)
        result = asyncio.run(supervisor.check())
        self.assertEqual(result["severity"], "FAILED")
        self.assertEqual(result["escalation_state"], "UNCERTAIN")
        self.assertFalse(result["verification_result"]["success"])

    def test_repeated_recovery_creates_no_workers_or_stores(self):
        clock = _Clock()
        tree = _ScreenMap()
        bus = CapabilityBus()
        bus.attach(screentree=tree)
        before = tuple(sorted(bus._ctx))
        supervisor = RuntimeHealthSupervisor(
            bus, self._engine(), clock=clock, interval_s=15)
        asyncio.run(supervisor.check())
        tree.rows = []
        clock.advance(16)
        asyncio.run(supervisor.check())
        self.assertEqual(tuple(sorted(bus._ctx)), before)
        self.assertNotIn("memory", bus._ctx)
        self.assertNotIn("procedure_store", bus._ctx)
        self.assertNotIn("supervisor", bus._ctx)
        self.assertFalse(hasattr(supervisor, "thread"))
        self.assertFalse(hasattr(supervisor, "_thread"))

    def test_cadence_and_retry_bounds_are_fixed(self):
        supervisor = RuntimeHealthSupervisor(
            CapabilityBus(), self._engine(), interval_s=9999, max_attempts=999)
        self.assertLessEqual(supervisor.interval_s, 300.0)
        self.assertEqual(supervisor.max_attempts, 2)

    def test_provenance_and_diagnosis_survive_result(self):
        clock = _Clock()
        hand = _Hand("AVAILABLE")
        bus = CapabilityBus()
        bus.attach(hand_tracker=hand)
        supervisor = RuntimeHealthSupervisor(
            bus, self._engine(), clock=clock, interval_s=15)
        asyncio.run(supervisor.check())
        hand.state = "UNAVAILABLE"
        hand.reason = "hardware disappeared"
        clock.advance(16)
        result = asyncio.run(supervisor.check())
        self.assertTrue(result["diagnosis"])
        self.assertTrue(result["reason"])
        self.assertTrue(result["provenance"])
        self.assertEqual(
            result["provenance"],
            "RuntimeHealthSupervisor+CapabilityBus+TaskEngine")


if __name__ == "__main__":
    unittest.main()
