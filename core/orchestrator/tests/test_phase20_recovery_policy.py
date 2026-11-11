"""Phase 20 recovery policy and human escalation governance tests."""
from __future__ import annotations

import asyncio
import unittest

from core.capability_bus import CapabilityBus
from core.orchestrator.task_engine import TaskEngine
from core.runtime_health import RuntimeHealthSupervisor
from core.runtime_recovery_policy import evaluate_recovery_policy
from core.runtime_repair import CapabilityRepairDecision
from unittest.mock import Mock


class _Hand:
    def __init__(self, state="AVAILABLE", reason=""):
        self.state = state
        self.reason = reason

    def status(self):
        return {"state": self.state, "reason": self.reason}


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


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecoveryPolicyTests(unittest.TestCase):
    def _approved(self):
        return CapabilityRepairDecision(
            capability="stt",
            problem="FAILED: worker exited",
            diagnosis="existing Ear warm-start",
            repairable=True,
            proposed_repair="ear.warm_start",
            risk="LOW",
            approved=True,
        )

    def test_low_risk_known_repair_allows_automatic_repair(self):
        policy = evaluate_recovery_policy(
            "stt", {"state": "FAILED", "reason": "worker exited"},
            self._approved(), configured_max_attempts=99)
        self.assertTrue(policy.automatic_repair_allowed)
        self.assertFalse(policy.escalation_required)
        self.assertEqual(policy.max_attempts, 2)
        self.assertEqual(policy.failure_class, "RECOVERABLE")

    def test_high_risk_or_ambiguous_condition_escalates(self):
        decision = CapabilityRepairDecision(
            capability="camera",
            problem="UNAVAILABLE: owner unclear",
            diagnosis="ambiguous owner state",
            repairable=True,
            proposed_repair="unknown",
            risk="HIGH",
            approved=False,
        )
        policy = evaluate_recovery_policy(
            "camera", {"state": "UNAVAILABLE", "reason": "owner unclear"},
            decision)
        self.assertFalse(policy.automatic_repair_allowed)
        self.assertTrue(policy.escalation_required)
        self.assertEqual(policy.escalation_level, "HIGH")
        self.assertIn("No specific user action is known", policy.escalation_message)

    def test_non_repairable_condition_escalates_without_repair(self):
        policy = evaluate_recovery_policy(
            "hand", {"state": "UNAVAILABLE", "reason": "camera stopped"},
            CapabilityRepairDecision(
                capability="hand", problem="UNAVAILABLE", diagnosis="none",
                repairable=False, proposed_repair="none",
                risk="UNSAFE_TO_REPAIR", approved=False))
        self.assertTrue(policy.escalation_required)
        self.assertFalse(policy.automatic_repair_allowed)
        self.assertEqual(policy.failure_class, "NON_REPAIRABLE")

    def test_bounded_recovery_failure_escalates_with_remaining_state(self):
        policy = evaluate_recovery_policy(
            "stt", {"state": "FAILED", "reason": "worker still dead"},
            self._approved(),
            recovery_result={
                "outcome": "FAILED",
                "attempts": 2,
                "post_repair_status": {
                    "state": "FAILED", "reason": "worker still dead"
                },
            })
        self.assertTrue(policy.escalation_required)
        self.assertFalse(policy.automatic_repair_allowed)
        self.assertEqual(policy.failure_class, "REPAIR_EXHAUSTED")
        self.assertIn("recovery failed", policy.escalation_message)

    def test_active_task_defers_policy(self):
        policy = evaluate_recovery_policy(
            "stt", {"state": "FAILED", "reason": "worker exited"},
            self._approved(), task_active=True)
        self.assertEqual(policy.failure_class, "TASK_ACTIVE")
        self.assertFalse(policy.escalation_required)
        self.assertFalse(policy.automatic_repair_allowed)
        self.assertEqual(policy.max_attempts, 0)

    def test_uncertain_status_never_allows_speculative_repair(self):
        policy = evaluate_recovery_policy(
            "stt", None, self._approved(), status_authoritative=False)
        self.assertEqual(policy.failure_class, "STATUS_UNCERTAIN")
        self.assertTrue(policy.escalation_required)
        self.assertFalse(policy.automatic_repair_allowed)
        self.assertIn("no speculative repair", policy.escalation_message)

    def test_escalation_dedup_and_meaningful_update_use_existing_notifier(self):
        clock = _Clock()
        hand = _Hand("AVAILABLE")
        bus = _OnlyBus(CapabilityBus(), {"hand"})
        bus.inner.attach(hand_tracker=hand)
        events = []
        supervisor = RuntimeHealthSupervisor(
            bus, None, interval_s=15, clock=clock,
            notifier=events.append)
        asyncio.run(supervisor.check())
        hand.state = "UNAVAILABLE"
        hand.reason = "hardware disappeared"
        clock.advance(16)
        asyncio.run(supervisor.check())
        self.assertEqual([event["kind"] for event in events], ["escalation"])
        clock.advance(16)
        asyncio.run(supervisor.check())
        self.assertEqual(len(events), 1)
        hand.reason = "device disconnected"
        clock.advance(16)
        asyncio.run(supervisor.check())
        self.assertEqual(len(events), 2)
        self.assertIn("device disconnected", events[-1]["message"])

    def test_verified_ready_clears_escalation_and_emits_one_clear_event(self):
        clock = _Clock()
        hand = _Hand("AVAILABLE")
        bus = _OnlyBus(CapabilityBus(), {"hand"})
        bus.inner.attach(hand_tracker=hand)
        events = []
        supervisor = RuntimeHealthSupervisor(
            bus, None, interval_s=15, clock=clock,
            notifier=events.append)
        asyncio.run(supervisor.check())
        hand.state = "UNAVAILABLE"
        hand.reason = "hardware disappeared"
        clock.advance(16)
        asyncio.run(supervisor.check())
        hand.state = "AVAILABLE"
        hand.reason = ""
        clock.advance(16)
        result = asyncio.run(supervisor.check())
        self.assertEqual(result["escalation_state"], "CLEARED")
        self.assertEqual([event["kind"] for event in events],
                         ["escalation", "cleared"])

    def test_policy_does_not_create_owners_stores_or_persistence(self):
        hand = _Hand("UNAVAILABLE", "camera stopped")
        bus = CapabilityBus()
        bus.attach(hand_tracker=hand)
        before = tuple(sorted(bus._ctx))
        policy = evaluate_recovery_policy(
            "hand", {"state": "UNAVAILABLE", "reason": "camera stopped"}, None)
        self.assertEqual(tuple(sorted(bus._ctx)), before)
        self.assertNotIn("memory", bus._ctx)
        self.assertNotIn("procedure_store", bus._ctx)
        self.assertNotIn("policy", bus._ctx)
        self.assertEqual(policy.provenance,
                         "RecoveryPolicy+runtime_repair+TaskEngine")

    def test_policy_retry_bound_is_fixed(self):
        policy = evaluate_recovery_policy(
            "stt", {"state": "FAILED", "reason": "worker exited"},
            self._approved(), configured_max_attempts=999)
        self.assertEqual(policy.max_attempts, 2)


if __name__ == "__main__":
    unittest.main()
