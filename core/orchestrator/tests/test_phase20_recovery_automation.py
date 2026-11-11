"""Phase 20 self-repair execution and recovery automation tests."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

from core.agent_state import AgentState
from core.capability_bus import CapabilityBus
from core.orchestrator.task_engine import TaskEngine


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
    def __init__(self, results=None):
        self._running = True
        self._mic_thread = _Thread(True)
        self._stt_thread = _Thread(True)
        self._stt_proc = _Proc(False)
        self.results = list(results or [True])
        self.warm_calls = 0

    def warm_start(self):
        self.warm_calls += 1
        result = self.results.pop(0) if self.results else False
        if result:
            self._stt_proc = _Proc(True)
        return result


class _HealthyEar(_Ear):
    def __init__(self):
        super().__init__(results=[])
        self._stt_proc = _Proc(True)


class _Hand:
    def status(self):
        return {"state": "UNAVAILABLE", "reason": "camera stopped"}


class _CountingBus:
    def __init__(self, inner):
        self.inner = inner
        self.status_calls = 0

    def capability_status(self, payload=None):
        self.status_calls += 1
        return self.inner.capability_status(payload or {})

    def __getattr__(self, name):
        return getattr(self.inner, name)


class RecoveryAutomationTests(unittest.TestCase):
    def _engine(self, state=None):
        return TaskEngine(
            Mock(), Mock(), Mock(), Mock(), Mock(),
            agent_state=state or AgentState(),
        )

    def test_healthy_capability_is_a_recorded_noop(self):
        ear = _HealthyEar()
        bus = CapabilityBus()
        bus.attach(ear=ear)
        state = AgentState()
        result = asyncio.run(self._engine(state).recover_capability(
            "stt", "healthy check", capability_bus=bus))
        self.assertEqual(result["outcome"], "NO_OP")
        self.assertFalse(result["repair"]["execution_result"]["attempted"])
        self.assertEqual(ear.warm_calls, 0)
        self.assertTrue(result["recovery_recorded"])
        self.assertIn("stt: NO_OP", state.snapshot()["recovery"])

    def test_repairable_failure_automatically_recovers_and_verifies(self):
        ear = _Ear([True])
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().recover_capability(
            "stt", "stt worker exited", capability_bus=bus))
        self.assertEqual(result["outcome"], "RECOVERED")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["post_repair_status"]["state"], "READY")
        self.assertTrue(result["verification_result"]["success"])
        self.assertEqual(ear.warm_calls, 1)

    def test_status_is_refreshed_after_each_attempt(self):
        ear = _Ear([False, True])
        bus = _CountingBus(CapabilityBus())
        bus.inner.attach(ear=ear)
        result = asyncio.run(self._engine().recover_capability(
            "stt", "worker died", capability_bus=bus, max_attempts=2))
        self.assertEqual(result["outcome"], "RECOVERED")
        self.assertEqual(result["attempts"], 2)
        self.assertGreaterEqual(bus.status_calls, 3)
        self.assertEqual(ear.warm_calls, 2)

    def test_recovery_stops_immediately_after_successful_verification(self):
        ear = _Ear([True, True])
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().recover_capability(
            "stt", capability_bus=bus, max_attempts=2))
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(ear.warm_calls, 1)

    def test_repair_failure_is_bounded_and_truthful(self):
        ear = _Ear([False, False, True])
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().recover_capability(
            "stt", capability_bus=bus, max_attempts=99))
        self.assertEqual(result["outcome"], "FAILED")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["post_repair_status"]["state"], "FAILED")
        self.assertEqual(ear.warm_calls, 2)

    def test_non_repairable_failure_is_not_attempted(self):
        bus = CapabilityBus()
        bus.attach(hand_tracker=_Hand())
        state = AgentState()
        result = asyncio.run(self._engine(state).recover_capability(
            "hand", "camera stopped", capability_bus=bus))
        self.assertEqual(result["outcome"], "UNREPAIRED")
        self.assertEqual(result["attempts"], 0)
        self.assertFalse(result["repair"]["approved"])
        self.assertTrue(result["recovery_recorded"])
        self.assertIn("hand: UNREPAIRED", state.snapshot()["recovery"])

    def test_repeated_recovery_is_idempotent_after_recovery(self):
        ear = _Ear([True])
        bus = CapabilityBus()
        bus.attach(ear=ear)
        engine = self._engine()
        first = asyncio.run(engine.recover_capability(
            "stt", capability_bus=bus))
        second = asyncio.run(engine.recover_capability(
            "stt", capability_bus=bus))
        self.assertEqual(first["outcome"], "RECOVERED")
        self.assertEqual(second["outcome"], "NO_OP")
        self.assertEqual(ear.warm_calls, 1)

    def test_recovery_preserves_problem_diagnosis_and_provenance(self):
        ear = _Ear([True])
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().recover_capability(
            "stt", "original worker failure", capability_bus=bus))
        self.assertEqual(result["problem"], "original worker failure")
        self.assertEqual(result["diagnosis"], result["repair"]["diagnosis"])
        self.assertEqual(result["provenance"], result["repair"]["provenance"])
        self.assertTrue(result["provenance"])

    def test_recovery_creates_no_owner_store_or_persistence(self):
        ear = _Ear([True])
        bus = CapabilityBus()
        bus.attach(ear=ear)
        before = tuple(sorted(bus._ctx))
        state = AgentState()
        asyncio.run(self._engine(state).recover_capability(
            "stt", capability_bus=bus))
        self.assertEqual(tuple(sorted(bus._ctx)), before)
        self.assertNotIn("memory", bus._ctx)
        self.assertNotIn("procedure_store", bus._ctx)
        self.assertNotIn("repair", bus._ctx)
        self.assertNotIn("recovery_store", bus._ctx)
        self.assertIn("recovery", state.snapshot())


if __name__ == "__main__":
    unittest.main()
