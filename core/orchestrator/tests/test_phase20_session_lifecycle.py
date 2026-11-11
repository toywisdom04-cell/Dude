"""Phase 20 session lifecycle and reliability reporting tests."""
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
        "completed_at": float(now),
        "capabilities_checked": list(capabilities),
        "changed_capabilities": list(changed),
        "degraded_capabilities": [],
        "severity": severity,
        "escalation_state": escalation,
        "recovery_result": list(recovery),
    }


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.state = AgentState(clock=self.clock)

    def record(self, result):
        return self.state.record_health_checkpoint(
            result, now=self.clock.now)

    def test_new_session_lifecycle_starts_deterministically(self):
        report = self.state.reliability_report()
        self.assertEqual(report["lifecycle"], "ACTIVE")
        self.assertEqual(report["session_started_at"], 100.0)
        self.assertIsNone(report["session_ended_at"])
        self.assertEqual(report["slo_state"], "INSUFFICIENT_DATA")
        self.assertIsNone(report["final_slo_state"])

    def test_active_session_reports_current_measurements(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(3)
        report = self.state.reliability_report()
        self.assertEqual(report["lifecycle"], "ACTIVE")
        self.assertEqual(report["current_uptime"], 3.0)
        self.assertEqual(report["health_checks_completed"], 1)
        self.assertIsNone(report["session_ended_at"])

    def test_healthy_session_finalizes_as_completed(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(5)
        report = self.state.finalize_session()
        self.assertEqual(report["lifecycle"], "COMPLETED")
        self.assertEqual(report["session_ended_at"], 105.0)
        self.assertEqual(report["final_slo_state"], "HEALTHY")
        self.assertIn("recovery_success_rate", report["insufficient_data"])

    def test_unresolved_degradation_finalizes_as_degraded(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "worker stalled",
            }],
            escalation="ESCALATED",
        ))
        self.clock.advance(4)
        report = self.state.finalize_session()
        self.assertEqual(report["lifecycle"], "DEGRADED")
        self.assertEqual(report["final_slo_state"], "AT_RISK")
        self.assertEqual(report["total_degraded_seconds"], 4.0)
        self.assertEqual(report["capability_incidents"], 1)

    def test_insufficient_authoritative_data_finalizes_incomplete(self):
        self.clock.advance(20)
        report = self.state.finalize_session()
        self.assertEqual(report["lifecycle"], "INCOMPLETE")
        self.assertEqual(report["final_slo_state"], "INSUFFICIENT_DATA")
        self.assertIsNone(report["metrics"]["recovery_success_rate"]["value"])

    def test_finalization_preserves_reliability_metrics(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "worker stalled",
            }],
            escalation="ESCALATED",
        ))
        self.clock.advance(3)
        self.record(_result(
            now=self.clock.now,
            recovery=[{
                "capability": "stt", "outcome": "RECOVERED", "attempts": 1,
            }],
            escalation="CLEARED",
        ))
        self.clock.advance(4)
        report = self.state.finalize_session()
        self.assertEqual(report["health_checks_completed"], 3)
        self.assertEqual(report["capability_incidents"], 1)
        self.assertEqual(report["capability_recoveries"], 1)
        self.assertEqual(report["failed_recoveries"], 0)
        self.assertEqual(report["escalations"], 1)
        self.assertEqual(report["incident_duration"], 3.0)
        self.assertEqual(report["recovery_latency"], 3.0)
        self.assertEqual(report["total_degraded_seconds"], 3.0)

    def test_finalization_records_exact_end_timestamp(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(12.5)
        report = self.state.finalize_session()
        self.assertEqual(report["session_started_at"], 100.0)
        self.assertEqual(report["session_ended_at"], 112.5)
        self.assertEqual(report["current_uptime"], 12.5)

    def test_repeated_finalization_is_idempotent_and_bounded(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        first = self.state.finalize_session()
        self.clock.advance(20)
        second = self.state.finalize_session()
        self.assertEqual(first, second)
        self.assertEqual(second["session_ended_at"], 102.0)
        self.assertEqual(second["current_uptime"], 2.0)
        self.assertEqual(second["health_checks_completed"], 1)

    def test_lifecycle_and_slo_remain_separate(self):
        self.record(_result(now=self.clock.now))
        active = self.state.reliability_report()
        self.assertEqual(active["lifecycle"], "ACTIVE")
        self.assertEqual(active["slo_state"], "HEALTHY")
        self.assertIsNone(active["final_slo_state"])

        self.clock.advance(2)
        self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "stalled",
            }],
            escalation="ESCALATED",
        ))
        at_risk = self.state.reliability_report()
        self.assertEqual(at_risk["lifecycle"], "ACTIVE")
        self.assertEqual(at_risk["slo_state"], "AT_RISK")
        self.assertIsNone(at_risk["final_slo_state"])

        self.clock.advance(1)
        final = self.state.finalize_session()
        self.assertEqual(final["lifecycle"], "DEGRADED")
        self.assertEqual(final["final_slo_state"], "AT_RISK")

    def test_measured_metrics_remain_measured(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(1)
        self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "stalled",
            }],
            escalation="ESCALATED",
        ))
        self.clock.advance(2)
        report = self.state.reliability_report()
        self.assertEqual(
            report["metrics"]["escalation_rate"]["availability"], "measured")
        self.assertEqual(
            report["capability_availability"]["stt"]["availability"],
            "measured")

    def test_insufficient_metrics_remain_none_with_availability(self):
        report = self.state.reliability_report()
        for name in (
                "recovery_success_rate", "recovery_failure_rate",
                "mean_recovery_latency", "degraded_time_ratio",
                "supervision_success_rate", "escalation_rate"):
            self.assertIsNone(report["metrics"][name]["value"])
            self.assertEqual(
                report["metrics"][name]["availability"],
                "insufficient_data")
        self.assertIn("recovery_success_rate", report["insufficient_data"])

    def test_existing_bounded_counters_remain_bounded(self):
        for _ in range(500):
            self.clock.advance(1)
            self.record(_result(now=self.clock.now))
        report = self.state.finalize_session()
        self.assertEqual(report["health_checks_completed"], 500)
        self.assertLessEqual(
            len(self.state._reliability_recovery_latencies), 32)
        self.assertLessEqual(len(self.state._reliability_capability_names), 24)
        self.assertLessEqual(len(self.state._reliability_active), 24)
        self.assertNotIn("reports", report)

    def test_recovery_statistics_are_unchanged_by_finalization(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        self.record(_result(
            now=self.clock.now,
            severity="DEGRADED",
            changed=[{
                "name": "stt", "from": "READY", "to": "DEGRADED",
                "reason": "stalled",
            }],
            escalation="ESCALATED",
        ))
        self.clock.advance(2)
        self.record(_result(
            now=self.clock.now,
            recovery=[{
                "capability": "stt", "outcome": "RECOVERED", "attempts": 1,
            }],
            escalation="CLEARED",
        ))
        before = self.state.reliability_snapshot()
        report = self.state.finalize_session()
        self.assertEqual(report["capability_recoveries"],
                         before["capability_recoveries"])
        self.assertEqual(report["recovery_latency"],
                         before["recovery_latency"])
        self.assertEqual(report["metrics"]["recovery_success_rate"]["value"],
                         before["metrics"]["recovery_success_rate"]["value"])

    def test_no_historical_session_store_is_introduced(self):
        self.assertFalse(hasattr(self.state, "_session_reports"))
        self.assertFalse(hasattr(self.state, "_finalized_reports"))
        self.state.finalize_session()
        self.assertFalse(hasattr(self.state, "_session_reports"))
        self.assertFalse(hasattr(self.state, "_finalized_reports"))

    def test_health_checkpoint_after_finalization_does_not_mutate_report(self):
        self.record(_result(now=self.clock.now))
        self.clock.advance(2)
        final = self.state.finalize_session()
        self.clock.advance(30)
        self.record(_result(now=self.clock.now))
        self.assertEqual(self.state.reliability_report(), final)


if __name__ == "__main__":
    unittest.main()
