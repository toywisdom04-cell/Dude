"""Phase 20 long-running stability and recovery SLO tests."""
from __future__ import annotations

import unittest

from core.agent_state import AgentState


class _Clock:
    def __init__(self, start=100.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _result(
    *,
    now,
    severity="HEALTHY",
    capabilities=("stt",),
    changed=(),
    recovery=(),
    escalation="NONE",
):
    return {
        "checked_at": float(now),
        "capabilities_checked": list(capabilities),
        "changed_capabilities": list(changed),
        "degraded_capabilities": [],
        "severity": severity,
        "escalation_state": escalation,
        "recovery_result": list(recovery),
    }


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.state = AgentState(clock=self.clock)

    def record(self, result):
        return self.state.record_health_checkpoint(result, now=self.clock.now)

    def test_session_starts_with_insufficient_data_metrics(self):
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertEqual(snapshot["health_checks_completed"], 0)
        self.assertEqual(snapshot["current_uptime"], 0.0)
        self.assertIsNone(snapshot["metrics"]["recovery_success_rate"]["value"])
        self.assertEqual(
            snapshot["metrics"]["recovery_success_rate"]["availability"],
            "insufficient_data")
        self.assertEqual(snapshot["slo"]["state"], "INSUFFICIENT_DATA")

    def test_healthy_checkpoints_do_not_create_incidents(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(10)
        snapshot = self.record(_result(now=self.clock.now))
        self.assertEqual(snapshot["capability_incidents"], 0)
        self.assertEqual(snapshot["escalations"], 0)
        self.assertEqual(snapshot["current_health_state"], "HEALTHY")

    def test_repeated_healthy_checks_remain_bounded(self):
        for _ in range(500):
            self.clock.advance(1)
            self.record(_result(now=self.clock.now))
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertEqual(snapshot["health_checks_completed"], 500)
        self.assertEqual(snapshot["capability_incidents"], 0)
        self.assertLessEqual(
            len(self.state._reliability_recovery_latencies), 32)
        self.assertLessEqual(len(self.state._reliability_capability_names), 24)
        self.assertEqual(len(self.state._reliability_active), 0)

    def test_ready_to_degraded_records_one_incident_and_escalation(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(5)
        snapshot = self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "worker stalled",
            }],
            escalation="ESCALATED",
        ))
        self.assertEqual(snapshot["capability_incidents"], 1)
        self.assertEqual(snapshot["escalations"], 1)
        self.assertEqual(snapshot["current_health_state"], "DEGRADED")
        self.assertEqual(snapshot["slo"]["state"], "AT_RISK")

    def test_recovery_records_success_and_latency(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(5)
        self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "worker stalled",
            }],
            escalation="ESCALATED",
        ))
        self.clock.advance(7)
        snapshot = self.record(_result(
            now=self.clock.now,
            recovery=[{
                "capability": "stt", "outcome": "RECOVERED", "attempts": 1,
            }],
            escalation="CLEARED",
        ))
        self.assertEqual(snapshot["capability_recoveries"], 1)
        self.assertEqual(snapshot["recovery_latency"], 7.0)
        self.assertEqual(snapshot["incident_duration"], 7.0)
        self.assertEqual(snapshot["total_degraded_seconds"], 7.0)
        self.assertEqual(snapshot["current_health_state"], "HEALTHY")
        self.assertEqual(snapshot["slo"]["state"], "HEALTHY")
        self.assertAlmostEqual(
            snapshot["metrics"]["recovery_success_rate"]["value"], 100.0)
        self.assertAlmostEqual(
            snapshot["availability_by_capability"]["stt"]["value"],
            100.0 * (1.0 - 7.0 / 12.0))

    def test_repeated_identical_unhealthy_state_does_not_create_incident(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        first = self.record(_result(
            now=self.clock.now, severity="DEGRADED",
            changed=[{"name": "stt", "from": "READY", "to": "DEGRADED",
                      "reason": "stalled"}],
            escalation="ESCALATED"))
        self.clock.advance(2)
        second = self.record(_result(
            now=self.clock.now, severity="DEGRADED",
            escalation="SUPPRESSED"))
        self.assertEqual(first["capability_incidents"], 1)
        self.assertEqual(second["capability_incidents"], 1)
        self.assertEqual(second["escalations"], 1)
        self.assertGreater(second["total_degraded_seconds"], 0.0)

    def test_failed_recovery_increments_failure_counter_once(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        snapshot = self.record(_result(
            now=self.clock.now, severity="FAILED",
            changed=[{"name": "stt", "from": "READY", "to": "FAILED",
                      "reason": "worker exited"}],
            recovery=[{
                "capability": "stt", "outcome": "FAILED", "attempts": 2,
            }],
            escalation="ESCALATED",
        ))
        self.assertEqual(snapshot["failed_recoveries"], 1)
        self.assertEqual(snapshot["capability_recoveries"], 0)
        self.assertEqual(snapshot["current_health_state"], "FAILED")
        self.assertEqual(snapshot["slo"]["state"], "BREACHED")

    def test_health_check_failure_and_uncertainty_are_counted(self):
        first = self.record(_result(
            now=self.clock.now, severity="FAILED", capabilities=(),
            escalation="UNCERTAIN"))
        self.clock.advance(5)
        second = self.record(_result(
            now=self.clock.now, severity="FAILED", capabilities=(),
            escalation="SUPPRESSED"))
        self.assertEqual(first["health_check_failures"], 1)
        self.assertEqual(second["health_check_failures"], 1)
        self.assertEqual(second["escalations"], 1)
        self.assertEqual(second["current_health_state"], "UNCERTAIN")

    def test_multiple_incidents_and_recovery_followed_by_new_incident(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(1)
        self.record(_result(
            now=self.clock.now, severity="DEGRADED",
            changed=[{"name": "stt", "from": "READY", "to": "DEGRADED",
                      "reason": "first"}], escalation="ESCALATED"))
        self.clock.advance(2)
        self.record(_result(
            now=self.clock.now,
            recovery=[{"capability": "stt", "outcome": "RECOVERED",
                       "attempts": 1}], escalation="CLEARED"))
        self.clock.advance(2)
        snapshot = self.record(_result(
            now=self.clock.now, severity="DEGRADED",
            changed=[{"name": "stt", "from": "READY", "to": "DEGRADED",
                      "reason": "second"}], escalation="ESCALATED"))
        self.assertEqual(snapshot["capability_incidents"], 2)
        self.assertEqual(snapshot["capability_recoveries"], 1)
        self.assertEqual(snapshot["escalations"], 2)

    def test_active_task_defer_does_not_count_health_check(self):
        snapshot = self.record(_result(
            now=self.clock.now, severity="INFO", capabilities=(),
            escalation="DEFERRED_ACTIVE_TASK"))
        self.assertEqual(snapshot["health_checks_completed"], 0)
        self.assertEqual(snapshot["current_health_state"], "INSUFFICIENT_DATA")

    def test_uptime_is_deterministic_under_fake_clock(self):
        self.clock.advance(42)
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertEqual(snapshot["current_uptime"], 42.0)
        self.clock.advance(8)
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertEqual(snapshot["current_uptime"], 50.0)

    def test_insufficient_samples_do_not_fake_percentages(self):
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertIsNone(snapshot["metrics"]["escalation_rate"]["value"])
        self.assertEqual(
            snapshot["metrics"]["escalation_rate"]["availability"],
            "insufficient_data")
        self.record(_result(now=self.clock.now))
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertEqual(snapshot["metrics"]["escalation_rate"]["value"], 0.0)
        self.assertEqual(
            snapshot["metrics"]["escalation_rate"]["availability"], "measured")

    def test_reliability_resets_for_new_session(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(5)
        self.record(_result(
            now=self.clock.now, severity="DEGRADED",
            changed=[{"name": "stt", "from": "READY", "to": "DEGRADED",
                      "reason": "stalled"}], escalation="ESCALATED"))
        self.clock.advance(5)
        snapshot = self.state.reset_reliability(now=self.clock.now)
        self.assertEqual(snapshot["current_uptime"], 0.0)
        self.assertEqual(snapshot["capability_incidents"], 0)
        self.assertEqual(snapshot["health_checks_completed"], 0)
        self.assertEqual(snapshot["slo"]["state"], "INSUFFICIENT_DATA")

    def test_reliability_survives_normal_checkpoints_without_new_store(self):
        for _ in range(10):
            self.clock.advance(1)
            self.record(_result(now=self.clock.now))
        snapshot = self.state.reliability_snapshot(now=self.clock.now)
        self.assertEqual(snapshot["health_checks_completed"], 10)
        self.assertEqual(snapshot["capability_incidents"], 0)
        self.assertEqual(snapshot["provenance"],
                         "AgentState+RuntimeHealthSupervisor+CapabilityBus")
        self.assertEqual(snapshot["slo"]["state"], "HEALTHY")


if __name__ == "__main__":
    unittest.main()
