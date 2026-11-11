"""Phase 20 runtime capability truth tests."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.agent_state import AgentState
from core.capability_bus import CapabilityBus
from core.orchestrator.goal_executor import CognitiveFrontDoor
from core.runtime_capability_status import (
    MAX_EVIDENCE,
    MAX_REASON,
    RuntimeCapabilityStatus,
    _state,
)


class _Alive:
    def is_alive(self):
        return True


class _Memory:
    def __init__(self, facts=None):
        self.conn = object()
        self._facts = list(facts or [])

    def recall_facts(self, query="", limit=8):
        return list(self._facts)

    def remember_fact(self, *args, **kwargs):
        return True


class _Voice:
    _thread = _Alive()
    silent = False
    _mixer_ready = True


class _Hand:
    def __init__(self, state, reason=""):
        self._state = state
        self._reason = reason

    def status(self):
        return {"state": self._state, "reason": self._reason}


class _ProcedureStore:
    def get_stats(self):
        return {"total_procedures": 0, "total_successes": 0,
                "total_failures": 0}


class _BadProcedureStore:
    def get_stats(self):
        raise RuntimeError("database unavailable")


class TestCapabilityStatusStates(unittest.TestCase):
    def test_all_state_precedence_is_explicit(self):
        self.assertEqual(
            _state(present=True, wired=False, active=False, ready=False),
            "PRESENT")
        self.assertEqual(
            _state(present=True, wired=True, active=False, ready=False),
            "WIRED")
        self.assertEqual(
            _state(present=True, wired=True, active=True, ready=False),
            "ACTIVE")
        self.assertEqual(
            _state(present=True, wired=True, active=True, ready=True),
            "READY")
        self.assertEqual(
            _state(present=True, wired=True, active=True, ready=True,
                   degraded=True), "DEGRADED")
        self.assertEqual(
            _state(present=True, wired=True, active=True, ready=True,
                   failed=True), "FAILED")
        self.assertEqual(
            _state(present=True, wired=True, active=True, ready=True,
                   unavailable=True), "UNAVAILABLE")
        self.assertEqual(
            _state(present=True, wired=False, active=False, ready=False,
                   inactive=True), "INACTIVE")

    def test_snapshot_is_bounded_and_has_no_persistent_owner(self):
        snapshot = RuntimeCapabilityStatus({}).snapshot()
        self.assertLessEqual(len(snapshot["capabilities"]), 24)
        for record in snapshot["capabilities"]:
            self.assertLessEqual(len(record["reason"]), MAX_REASON)
            self.assertLessEqual(len(record["evidence"]), MAX_EVIDENCE)
            self.assertIn(record["state"], {
                "PRESENT", "WIRED", "ACTIVE", "READY", "DEGRADED",
                "FAILED", "UNAVAILABLE", "INACTIVE"})

    def test_inactive_optional_component_is_not_unavailable(self):
        record = next(r for r in RuntimeCapabilityStatus({}).snapshot()
                      ["capabilities"] if r["name"] == "hand")
        self.assertTrue(record["present"])
        self.assertEqual(record["state"], "INACTIVE")
        self.assertNotIn("disconnected", record["reason"].lower())

    def test_hand_owner_state_is_preserved(self):
        degraded = RuntimeCapabilityStatus({
            "hand_tracker": _Hand("DEGRADED", "camera model unavailable")
        }).snapshot()
        record = next(r for r in degraded["capabilities"]
                      if r["name"] == "hand")
        self.assertEqual(record["state"], "DEGRADED")
        self.assertTrue(record["active"])
        self.assertFalse(record["ready"])

    def test_procedure_probe_failure_is_failed(self):
        snapshot = RuntimeCapabilityStatus({
            "procedure_store": _BadProcedureStore()
        }).snapshot()
        record = next(r for r in snapshot["capabilities"]
                      if r["name"] == "procedures")
        self.assertEqual(record["state"], "FAILED")

    def test_ready_owner_is_readiness_not_object_presence(self):
        snapshot = RuntimeCapabilityStatus({
            "voice": _Voice(), "memory": _Memory(["fact"])
        }).snapshot()
        states = {r["name"]: r for r in snapshot["capabilities"]}
        self.assertEqual(states["tts"]["state"], "READY")
        self.assertEqual(states["memory"]["state"], "READY")
        self.assertTrue(states["tts"]["ready"])
        self.assertTrue(states["tts"]["active"])


class TestCapabilityBusTruthBoundary(unittest.TestCase):
    def test_memory_facts_survive_bus_to_front_door(self):
        bus = CapabilityBus()
        bus.attach(memory=_Memory(["remembered fact"]), ask_user=lambda *a: True)
        result = bus.request("MEMORY_RECALL", {"query": "remembered", "limit": 5})
        self.assertTrue(result["success"])
        self.assertEqual(result["data"][0]["content"], "remembered fact")

        front = CognitiveFrontDoor(bus, AgentState())
        bundle = front._get_relevant_memory("remembered")
        self.assertEqual(bundle.facts[0]["content"], "remembered fact")

    def test_verification_and_recovery_use_boot_attachment_names(self):
        bus = CapabilityBus()
        verification = object()
        recovery = object()
        bus.attach(verification=verification, recovery=recovery)
        self.assertIs(bus.verification, verification)
        self.assertIs(bus.recovery, recovery)

    def test_front_door_uses_current_bus_truth_not_hardcoded_voice(self):
        bus = CapabilityBus()
        front = CognitiveFrontDoor(bus, AgentState())
        self.assertEqual(front._self_model["capability_states"]["tts"],
                         "INACTIVE")
        self.assertEqual(front._self_model["capability_states"]["stt"],
                         "INACTIVE")
        self.assertNotIn("tts", front._self_model["capabilities_available"])
        self.assertNotIn("stt", front._self_model["capabilities_available"])

        bus.attach(voice=_Voice())
        refreshed = front._build_self_model()
        self.assertEqual(refreshed["capability_states"]["tts"], "READY")

    def test_task_specific_tool_failure_is_degraded_not_missing(self):
        import core.tools as tools
        bus = CapabilityBus()
        bus.attach(memory=_Memory(), ask_user=lambda *a: True)
        with patch.object(tools, "PLATFORM", object()):
            record = next(r for r in bus.capability_status()["data"]["capabilities"]
                          if r["name"] == "computer_tools")
        self.assertEqual(record["state"], "DEGRADED")
        self.assertTrue(record["present"])
        self.assertTrue(record["wired"])
        self.assertFalse(record["failed"])
        self.assertFalse(record["unavailable"])

    def test_known_task_planning_contract_reports_brain_inactive_when_idle(self):
        class _Provider:
            name = "test"
            vision = False
        class _Brain:
            providers = [_Provider()]
            active_provider = None
            def chat(self, *args, **kwargs):
                return ""
            def think(self, *args, **kwargs):
                return ""
            def has_vision(self):
                return False
        class _Router:
            def route(self, *args, **kwargs):
                return None
        bus = CapabilityBus()
        bus.attach(brain=_Brain(), intelligence_router=_Router(),
                   task_engine=object())
        record = next(r for r in bus.capability_status()["data"]["capabilities"]
                      if r["name"] == "brain_routing")
        self.assertEqual(record["state"], "INACTIVE")
        self.assertNotIn("decomposition", record["reason"])
        self.assertIn("model_decomposition_ready=True", record["evidence"])


if __name__ == "__main__":
    unittest.main()
