"""Phase 20 self-repair decision and readiness tests."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

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
    def __init__(self, alive=True, repair_result=True):
        self._running = True
        self._mic_thread = _Thread(True)
        self._stt_thread = _Thread(True)
        self._stt_proc = _Proc(alive)
        self.repair_result = repair_result
        self.warm_calls = 0

    def warm_start(self):
        self.warm_calls += 1
        if self.repair_result:
            self._stt_proc = _Proc(True)
        return self.repair_result


class _Perception:
    def observe(self, *args, **kwargs):
        return object()


class _PerceptionService:
    def __init__(self):
        self._fast_thread = _Thread(False)
        self.start_calls = 0

    def start(self):
        self.start_calls += 1
        self._fast_thread = _Thread(True)


class _ScreenMap:
    def __init__(self):
        self._thread = _Thread(True)
        self.rows = []
        self.refresh_calls = 0

    def available(self):
        return True

    def current_view(self, limit=10):
        return "ok"

    def refresh_sync(self):
        self.refresh_calls += 1
        self.rows = [{"name": "refreshed"}]
        return True


class _Hand:
    def status(self):
        return {"state": "UNAVAILABLE", "reason": "camera stopped"}


class RepairReadinessTests(unittest.TestCase):
    def _engine(self):
        return TaskEngine(Mock(), Mock(), Mock(), Mock(), Mock())

    def test_repairable_stt_issue_is_repaired_and_freshly_verified(self):
        ear = _Ear(alive=False)
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().repair_capability(
            "stt", capability_bus=bus))
        self.assertTrue(result["repairable"])
        self.assertEqual(result["proposed_repair"], "ear.warm_start")
        self.assertEqual(result["risk"], "LOW")
        self.assertTrue(result["execution_result"]["success"])
        self.assertTrue(result["verification_result"]["success"])
        self.assertEqual(result["post_repair_status"]["state"], "READY")
        self.assertEqual(ear.warm_calls, 1)
        for key in (
            "capability", "problem", "diagnosis", "repairable",
            "proposed_repair", "risk", "execution_result",
            "post_repair_status", "verification_result", "provenance",
        ):
            self.assertIn(key, result)

    def test_perception_start_is_an_existing_idempotent_repair(self):
        service = _PerceptionService()
        bus = CapabilityBus()
        bus.attach(perception=_Perception(), perception_service=service)
        result = asyncio.run(self._engine().repair_capability(
            "perception", capability_bus=bus))
        self.assertEqual(result["proposed_repair"], "perception_service.start")
        self.assertTrue(result["verification_result"]["success"])
        self.assertEqual(result["post_repair_status"]["state"], "READY")
        self.assertEqual(service.start_calls, 1)

    def test_uia_refresh_is_a_low_risk_repair(self):
        tree = _ScreenMap()
        bus = CapabilityBus()
        bus.attach(screentree=tree)
        result = asyncio.run(self._engine().repair_capability(
            "uia", capability_bus=bus))
        self.assertEqual(result["proposed_repair"], "screentree.refresh_sync")
        self.assertTrue(result["verification_result"]["success"])
        self.assertEqual(result["post_repair_status"]["state"], "READY")
        self.assertEqual(tree.refresh_calls, 1)

    def test_non_repairable_issue_is_not_executed(self):
        bus = CapabilityBus()
        bus.attach(hand_tracker=_Hand())
        result = asyncio.run(self._engine().repair_capability(
            "hand", capability_bus=bus))
        self.assertFalse(result["repairable"])
        self.assertFalse(result["approved"])
        self.assertEqual(result["proposed_repair"], "none")
        self.assertEqual(result["attempts"], 0)
        self.assertFalse(result["verification_result"]["success"])

    def test_already_healthy_capability_has_no_repair(self):
        ear = _Ear(alive=True)
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().repair_capability(
            "stt", capability_bus=bus))
        self.assertEqual(result["post_repair_status"]["state"], "READY")
        self.assertFalse(result["execution_result"]["attempted"])
        self.assertTrue(result["verification_result"]["success"])
        self.assertEqual(ear.warm_calls, 0)

    def test_failed_repair_is_bounded_and_truthful(self):
        ear = _Ear(alive=False, repair_result=False)
        bus = CapabilityBus()
        bus.attach(ear=ear)
        result = asyncio.run(self._engine().repair_capability(
            "stt", capability_bus=bus, max_attempts=99))
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(ear.warm_calls, 2)
        self.assertFalse(result["execution_result"]["success"])
        self.assertFalse(result["verification_result"]["success"])
        self.assertEqual(result["post_repair_status"]["state"], "FAILED")

    def test_repair_does_not_create_unrelated_owner_or_store(self):
        ear = _Ear(alive=False)
        bus = CapabilityBus()
        bus.attach(ear=ear)
        before = tuple(sorted(bus._ctx))
        asyncio.run(self._engine().repair_capability("stt", capability_bus=bus))
        self.assertEqual(tuple(sorted(bus._ctx)), before)
        self.assertNotIn("memory", bus._ctx)
        self.assertNotIn("procedure_store", bus._ctx)
        self.assertNotIn("repair", bus._ctx)


if __name__ == "__main__":
    unittest.main()
