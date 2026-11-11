"""DUDE authoritative agent state (single facade).

One canonical runtime truth for: current turn, full goal, active task,
step, progress, application/window/focus, screen facts, OCR facts, visual
evidence metadata, verification truth, recovery state, corrections, last
result, confidence, provenance, continuation context, and bounded session
reliability signals.

Subsystems keep their internal objects where necessary, but AgentState is
the synchronization point: nobody guesses which task state is real.
Thread-safe. Dependency-free (stdlib only) so any layer can import it.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Optional


RELIABILITY_PROVENANCE = "AgentState+RuntimeHealthSupervisor+CapabilityBus"
RELIABILITY_MAX_ATTEMPTS = 2
RELIABILITY_MAX_INTERVAL = 300.0
RELIABILITY_MAX_LATENCIES = 32
RELIABILITY_MAX_CAPABILITIES = 24
SESSION_LIFECYCLE_STATES = {"ACTIVE", "COMPLETED", "DEGRADED", "INCOMPLETE"}


@dataclass(frozen=True)
class TaskContinuityContext:
    """Bounded current-task handoff evidence, not a second task state."""

    goal: str
    task_id: str
    task: str
    step: str
    step_index: int
    step_total: int
    application: str
    window: Dict[str, Any]
    focus: str
    last_verified_progress: Dict[str, Any]
    active_recovery_state: str
    interruption_reason: str
    capability: str
    interruption_state: str
    safe_to_resume: bool
    resume_decision: str
    continuation_provenance: str
    task_state: str
    captured_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "task_id": self.task_id,
            "task": self.task,
            "step": self.step,
            "step_index": self.step_index,
            "step_total": self.step_total,
            "application": self.application,
            "window": dict(self.window),
            "focus": self.focus,
            "last_verified_progress": dict(self.last_verified_progress),
            "active_recovery_state": self.active_recovery_state,
            "interruption_reason": self.interruption_reason,
            "capability": self.capability,
            "interruption_state": self.interruption_state,
            "safe_to_resume": bool(self.safe_to_resume),
            "resume_decision": self.resume_decision,
            "continuation_provenance": self.continuation_provenance,
            "task_state": self.task_state,
            "captured_at": float(self.captured_at),
        }


@dataclass(frozen=True)
class ReliabilityMetric:
    value: Any
    sample_count: int
    availability: str
    measurement_interval: float
    provenance: str = RELIABILITY_PROVENANCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "sample_count": int(self.sample_count),
            "availability": self.availability,
            "measurement_interval": float(self.measurement_interval),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ReliabilitySLO:
    state: str
    reason: str
    thresholds: Dict[str, Any]
    provenance: str = RELIABILITY_PROVENANCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "thresholds": dict(self.thresholds),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ReliabilitySnapshot:
    session_started_at: float
    current_uptime: float
    last_health_check: float
    health_checks_completed: int
    health_check_failures: int
    capability_incidents: int
    capability_recoveries: int
    failed_recoveries: int
    escalations: int
    total_degraded_seconds: float
    incident_duration: Optional[float]
    recovery_latency: Optional[float]
    task_failures_due_to_runtime_health: int
    current_health_state: str
    metrics: Dict[str, ReliabilityMetric]
    availability_by_capability: Dict[str, ReliabilityMetric]
    slo: ReliabilitySLO
    provenance: str = RELIABILITY_PROVENANCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_started_at": float(self.session_started_at),
            "current_uptime": float(self.current_uptime),
            "last_health_check": float(self.last_health_check),
            "health_checks_completed": int(self.health_checks_completed),
            "health_check_failures": int(self.health_check_failures),
            "capability_incidents": int(self.capability_incidents),
            "capability_recoveries": int(self.capability_recoveries),
            "failed_recoveries": int(self.failed_recoveries),
            "escalations": int(self.escalations),
            "total_degraded_seconds": float(self.total_degraded_seconds),
            "incident_duration": self.incident_duration,
            "recovery_latency": self.recovery_latency,
            "task_failures_due_to_runtime_health": int(
                self.task_failures_due_to_runtime_health),
            "current_health_state": self.current_health_state,
            "metrics": {key: value.to_dict()
                        for key, value in self.metrics.items()},
            "availability_by_capability": {
                key: value.to_dict()
                for key, value in self.availability_by_capability.items()
            },
            "slo": self.slo.to_dict(),
            "provenance": self.provenance,
        }


class AgentState:
    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._lock = threading.Lock()
        self._clock = clock or time.time
        self._turn_id = 0
        self._turn_text = ""
        self._goal = ""
        self._purpose = ""
        self._task = ""
        self._step = ""
        self._step_index = 0
        self._step_total = 0
        self._progress = ""
        self._app = ""
        self._window = ""
        self._focus = ""
        self._screen = ""
        self._ocr = ""
        self._ocr_at = 0.0
        self._visual = ""
        self._verified = None
        self._verify_note = ""
        self._recovery = ""
        self._retries = 0
        self._correction = ""
        self._result = ""
        self._confidence = 0.0
        self._provenance = ""
        self._updated = time.time()
        self._reset_reliability_locked(float(self._clock()))

    # ---------------- turn / goal ----------------
    def set_goal(self, goal: str, purpose: str = "") -> None:
        with self._lock:
            self._turn_id += 1
            self._turn_text = goal or ""
            self._goal = goal or ""
            self._purpose = purpose or ""
            self._updated = time.time()

    def get_goal(self) -> str:
        with self._lock:
            return self._goal

    # ---------------- task / step ----------------
    def update_task(self, task: str = "", progress: str = "") -> None:
        with self._lock:
            if task:
                self._task = task
            if progress:
                self._progress = progress
            self._updated = time.time()

    def set_step(self, desc: str, index: int = 0, total: int = 0) -> None:
        with self._lock:
            self._step = desc or ""
            self._step_index = index
            self._step_total = total
            self._updated = time.time()

    # ---------------- screen / perception ----------------
    def set_screen_context(self, app: str = "", window: str = "",
                           focus: str = "", screen: str = "",
                           ocr: str = "", visual: str = "") -> None:
        with self._lock:
            if app:
                self._app = app
            if window:
                self._window = window
            if focus:
                self._focus = focus
            if screen:
                self._screen = screen
            if ocr:
                self._ocr = ocr
                self._ocr_at = time.time()
            if visual:
                self._visual = visual
            self._updated = time.time()

    # ---------------- verification / recovery ----------------
    def set_verification(self, ok: Optional[bool], note: str = "") -> None:
        with self._lock:
            self._verified = ok
            self._verify_note = note or ""
            self._updated = time.time()

    def set_recovery(self, note: str, retries: int = 0) -> None:
        with self._lock:
            self._recovery = note or ""
            self._retries = retries
            self._updated = time.time()

    # ---------------- correction / result ----------------
    def set_correction(self, text: str) -> None:
        with self._lock:
            self._correction = text or ""
            self._updated = time.time()

    def set_result(self, text: str, confidence: float = 0.0,
                   provenance: str = "") -> None:
        with self._lock:
            self._result = text or ""
            self._confidence = confidence
            self._provenance = provenance or ""
            self._updated = time.time()

    def clear_active_goal(self) -> None:
        with self._lock:
            self._goal = ""
            self._purpose = ""
            self._task = ""
            self._step = ""
            self._step_index = 0
            self._step_total = 0
            self._progress = ""
            self._verified = None
            self._verify_note = ""
            self._recovery = ""
            self._retries = 0
            self._correction = ""
            self._updated = time.time()

    # ---------------- bounded task continuity ----------------
    @staticmethod
    def _continuity_text(value: Any, limit: int) -> str:
        return str(value or "").replace("\n", " ").strip()[:limit]

    @staticmethod
    def _continuity_window(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = ("app", "title", "hwnd", "focused_name", "focus")
        result = {}
        for key in allowed:
            if key not in value:
                continue
            item = value.get(key)
            if isinstance(item, (str, int, float, bool)) or item is None:
                result[key] = item
        return result

    def capture_task_continuity(
        self,
        context: Dict[str, Any],
        *,
        capability: str,
        reason: str,
        active_recovery_state: str = "WAITING_FOR_RUNTIME_RECOVERY",
        interruption_state: str = "WAITING_FOR_RECOVERY",
        safe_to_resume: bool = False,
        resume_decision: str = "HANDOFF_REQUIRED",
        provenance: str = "TaskEngine+AgentState+RuntimeHealthSupervisor",
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Store one bounded projection of the authoritative TaskEngine state."""
        if not isinstance(context, dict):
            return {}
        with self._lock:
            captured_at = float(self._clock() if now is None else now)
            task_id = self._continuity_text(context.get("task_id"), 96)
            current_capability = self._continuity_text(capability, 64)
            verified = context.get("last_verified_progress")
            if not isinstance(verified, dict):
                verified = {}
            verified = {
                "verified": bool(verified.get("verified", False)),
                "method": self._continuity_text(verified.get("method"), 80),
                "evidence": self._continuity_text(verified.get("evidence"), 240),
                "action_status": self._continuity_text(
                    verified.get("action_status"), 40),
            }
            previous = self._task_continuity
            same_incident = bool(
                previous is not None
                and previous.task_id == task_id
                and previous.capability == current_capability
            )
            record = TaskContinuityContext(
                goal=self._continuity_text(context.get("goal"), 280),
                task_id=task_id,
                task=self._continuity_text(
                    context.get("task") or context.get("goal"), 280),
                step=self._continuity_text(context.get("step"), 240),
                step_index=max(0, int(context.get("step_index", 0) or 0)),
                step_total=max(0, int(context.get("step_total", 0) or 0)),
                application=self._continuity_text(context.get("application"), 120),
                window=self._continuity_window(context.get("window")),
                focus=self._continuity_text(context.get("focus"), 160),
                last_verified_progress=verified,
                active_recovery_state=self._continuity_text(
                    active_recovery_state, 96),
                interruption_reason=self._continuity_text(reason, 280),
                capability=current_capability,
                interruption_state=self._continuity_text(
                    interruption_state, 64),
                safe_to_resume=bool(safe_to_resume),
                resume_decision=self._continuity_text(
                    resume_decision, 64),
                continuation_provenance=self._continuity_text(
                    provenance, 160),
                task_state=self._continuity_text(
                    context.get("task_state"), 64),
                captured_at=(previous.captured_at if same_incident and previous
                             else captured_at),
            )
            self._task_continuity = record
            return record.to_dict()

    def task_continuity(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._task_continuity is None:
                return None
            return copy.deepcopy(self._task_continuity.to_dict())

    def clear_task_continuity(self) -> None:
        with self._lock:
            self._task_continuity = None

    def record_action_fence(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Keep one compact action-fence decision for current status/reporting."""
        if not isinstance(decision, dict):
            return {}
        required = decision.get("required_capabilities") or []
        if not isinstance(required, (list, tuple, set)):
            required = []
        record = {
            "state": self._continuity_text(decision.get("state"), 40),
            "decision": self._continuity_text(decision.get("decision"), 40),
            "allowed": bool(decision.get("allowed", False)),
            "reason": self._continuity_text(decision.get("reason"), 280),
            "required_capabilities": [
                self._continuity_text(item, 64) for item in list(required)[:24]
            ],
            "blocking_capability": self._continuity_text(
                decision.get("blocking_capability"), 64),
            "task_step": self._continuity_text(
                decision.get("task_step"), 240),
            "verification_basis": self._continuity_text(
                decision.get("verification_basis"), 160),
            "provenance": self._continuity_text(
                decision.get("provenance"), 160),
            "checked_at": float(decision.get("checked_at", 0.0) or 0.0),
        }
        with self._lock:
            self._action_fence = record
            return copy.deepcopy(record)

    def action_fence(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._action_fence is None:
                return None
            return copy.deepcopy(self._action_fence)

    def record_task_handoff(self, handoff: Dict[str, Any]) -> Dict[str, Any]:
        """Keep one bounded current user-handoff request, never a history."""
        if not isinstance(handoff, dict):
            return {}
        record = {
            "state": self._continuity_text(handoff.get("state"), 48),
            "task_id": self._continuity_text(handoff.get("task_id"), 96),
            "task": self._continuity_text(handoff.get("task"), 280),
            "action_type": self._continuity_text(
                handoff.get("action_type"), 80),
            "step": self._continuity_text(handoff.get("step"), 240),
            "side_effect_class": self._continuity_text(
                handoff.get("side_effect_class"), 40),
            "replay_policy": self._continuity_text(
                handoff.get("replay_policy"), 48),
            "reason": self._continuity_text(handoff.get("reason"), 280),
            "message": self._continuity_text(handoff.get("message"), 280),
            "provenance": self._continuity_text(
                handoff.get("provenance"), 160),
        }
        with self._lock:
            self._task_handoff = record
            return copy.deepcopy(record)

    def task_handoff(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._task_handoff is None:
                return None
            return copy.deepcopy(self._task_handoff)

    # ---------------- bounded reliability ----------------
    def _reset_reliability_locked(self, now: float) -> None:
        self._reliability_started_at = float(now)
        self._reliability_last_check = 0.0
        self._reliability_health_checks = 0
        self._reliability_health_failures = 0
        self._reliability_incidents = 0
        self._reliability_recoveries = 0
        self._reliability_failed_recoveries = 0
        self._reliability_escalations = 0
        self._reliability_degraded_seconds = 0.0
        self._reliability_last_incident_duration = None
        self._reliability_recovery_latencies: Deque[float] = deque(
            maxlen=RELIABILITY_MAX_LATENCIES)
        self._reliability_last_recovery_latency = None
        self._reliability_active: Dict[str, Dict[str, Any]] = {}
        self._reliability_capability_seconds: Dict[str, float] = {}
        self._reliability_capability_names = set()
        self._reliability_escalation_keys = set()
        self._reliability_task_failures = 0
        self._reliability_health_state = "INSUFFICIENT_DATA"
        self._session_lifecycle = "ACTIVE"
        self._session_ended_at = None
        self._finalized_report = None
        self._task_continuity: Optional[TaskContinuityContext] = None
        self._action_fence: Optional[Dict[str, Any]] = None
        self._task_handoff: Optional[Dict[str, Any]] = None

    def reset_reliability(self, now: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            reset_at = float(self._clock() if now is None else now)
            self._reset_reliability_locked(reset_at)
            return self._reliability_snapshot_locked(reset_at).to_dict()

    def _account_reliability_locked(self, now: float) -> None:
        for capability, incident in list(self._reliability_active.items()):
            delta = max(0.0, now - float(
                incident.get("last_accounted_at", incident["started_at"])))
            self._reliability_degraded_seconds += delta
            self._reliability_capability_seconds[capability] = (
                self._reliability_capability_seconds.get(capability, 0.0) + delta)
            incident["last_accounted_at"] = now

    def _open_reliability_incident_locked(self, capability: str,
                                          state: str, reason: str,
                                          now: float) -> None:
        if capability in self._reliability_active:
            return
        self._reliability_active[capability] = {
            "started_at": now,
            "last_accounted_at": now,
            "state": state,
            "reason": reason,
        }
        self._reliability_incidents += 1

    def _close_reliability_incident_locked(self, capability: str,
                                           now: float,
                                           recovered: bool) -> bool:
        self._account_reliability_locked(now)
        incident = self._reliability_active.pop(capability, None)
        if incident is None:
            return False
        duration = max(0.0, now - float(incident["started_at"]))
        self._reliability_last_incident_duration = duration
        self._reliability_escalation_keys.discard(capability)
        if recovered:
            self._reliability_recoveries += 1
            self._reliability_recovery_latencies.append(duration)
            self._reliability_last_recovery_latency = duration
        return True

    @staticmethod
    def _metric(value: Any, sample_count: int, availability: str,
                interval: float) -> ReliabilityMetric:
        return ReliabilityMetric(
            value=value,
            sample_count=sample_count,
            availability=availability,
            measurement_interval=max(0.0, float(interval)),
        )

    def _reliability_snapshot_locked(self, now: float) -> ReliabilitySnapshot:
        uptime = max(0.0, now - self._reliability_started_at)
        capability_names = sorted(self._reliability_capability_names)[
            :RELIABILITY_MAX_CAPABILITIES]
        availability: Dict[str, ReliabilityMetric] = {}
        for capability in capability_names:
            degraded = self._reliability_capability_seconds.get(capability, 0.0)
            if uptime > 0.0 and self._reliability_health_checks > 0:
                value = max(0.0, min(
                    100.0, 100.0 * (1.0 - degraded / uptime)))
                availability[capability] = self._metric(
                    value, self._reliability_health_checks,
                    "measured", uptime)
            else:
                availability[capability] = self._metric(
                    None, self._reliability_health_checks,
                    "insufficient_data", uptime)

        outcomes = self._reliability_recoveries + \
            self._reliability_failed_recoveries
        metrics: Dict[str, ReliabilityMetric] = {}
        metrics["recovery_success_rate"] = self._metric(
            (100.0 * self._reliability_recoveries / outcomes)
            if outcomes else None,
            outcomes,
            "measured" if outcomes else "insufficient_data",
            uptime,
        )
        metrics["recovery_failure_rate"] = self._metric(
            (100.0 * self._reliability_failed_recoveries / outcomes)
            if outcomes else None,
            outcomes,
            "measured" if outcomes else "insufficient_data",
            uptime,
        )
        latencies = list(self._reliability_recovery_latencies)
        metrics["mean_recovery_latency"] = self._metric(
            (sum(latencies) / len(latencies)) if latencies else None,
            len(latencies),
            "measured" if latencies else "insufficient_data",
            uptime,
        )
        metrics["last_recovery_latency"] = self._metric(
            self._reliability_last_recovery_latency,
            len(latencies),
            "measured" if latencies else "insufficient_data",
            uptime,
        )
        denominator = uptime * max(1, len(capability_names))
        metrics["degraded_time_ratio"] = self._metric(
            (self._reliability_degraded_seconds / denominator)
            if uptime > 0.0 and capability_names else None,
            self._reliability_health_checks,
            "measured" if uptime > 0.0 and capability_names
            else "insufficient_data",
            uptime,
        )
        checks = self._reliability_health_checks
        metrics["supervision_success_rate"] = self._metric(
            (100.0 * (checks - self._reliability_health_failures) / checks)
            if checks else None,
            checks,
            "measured" if checks else "insufficient_data",
            uptime,
        )
        metrics["escalation_rate"] = self._metric(
            (100.0 * self._reliability_escalations / checks)
            if checks else None,
            checks,
            "measured" if checks else "insufficient_data",
            uptime,
        )
        metrics["runtime_health_task_interruption_count"] = self._metric(
            self._reliability_task_failures,
            checks,
            "observed" if checks else "insufficient_data",
            uptime,
        )

        gap = (max(0.0, now - self._reliability_last_check)
               if self._reliability_last_check else uptime)
        if self._reliability_health_state == "INSUFFICIENT_DATA":
            slo_state = "INSUFFICIENT_DATA"
            slo_reason = "no authoritative health checkpoint recorded"
        elif (self._reliability_health_state in {"FAILED", "UNCERTAIN"} or
              any(inc.get("state") == "FAILED"
                  for inc in self._reliability_active.values())):
            slo_state = "BREACHED"
            slo_reason = "an authoritative runtime capability remains failed"
        elif (self._reliability_active or self._reliability_failed_recoveries or
              self._reliability_health_failures or
              gap > 2.0 * RELIABILITY_MAX_INTERVAL):
            slo_state = "AT_RISK"
            slo_reason = "degradation, recovery failure, or supervision gap observed"
        else:
            slo_state = "HEALTHY"
            slo_reason = "no unresolved reliability incident observed"

        return ReliabilitySnapshot(
            session_started_at=self._reliability_started_at,
            current_uptime=uptime,
            last_health_check=self._reliability_last_check,
            health_checks_completed=checks,
            health_check_failures=self._reliability_health_failures,
            capability_incidents=self._reliability_incidents,
            capability_recoveries=self._reliability_recoveries,
            failed_recoveries=self._reliability_failed_recoveries,
            escalations=self._reliability_escalations,
            total_degraded_seconds=self._reliability_degraded_seconds,
            incident_duration=self._reliability_last_incident_duration,
            recovery_latency=self._reliability_last_recovery_latency,
            task_failures_due_to_runtime_health=self._reliability_task_failures,
            current_health_state=self._reliability_health_state,
            metrics=metrics,
            availability_by_capability=availability,
            slo=ReliabilitySLO(
                state=slo_state,
                reason=slo_reason,
                thresholds={
                    "max_recovery_attempts": RELIABILITY_MAX_ATTEMPTS,
                    "max_supervision_interval_seconds": RELIABILITY_MAX_INTERVAL,
                },
            ),
        )

    def _build_reliability_report_locked(self, now: float,
                                          lifecycle: Optional[str] = None,
                                          ended_at: Optional[float] = None
                                          ) -> Dict[str, Any]:
        """Derive the compact current-session report from one snapshot."""
        snapshot = self._reliability_snapshot_locked(now).to_dict()
        current_lifecycle = lifecycle or self._session_lifecycle
        if current_lifecycle not in SESSION_LIFECYCLE_STATES:
            current_lifecycle = "INCOMPLETE"
        insufficient = sorted(
            key for key, metric in snapshot["metrics"].items()
            if metric.get("availability") == "insufficient_data"
        )
        insufficient.extend(
            "availability_by_capability.%s" % name
            for name, metric in snapshot["availability_by_capability"].items()
            if metric.get("availability") == "insufficient_data"
        )
        return {
            "lifecycle": current_lifecycle,
            "session_started_at": snapshot["session_started_at"],
            "session_ended_at": ended_at,
            "current_uptime": snapshot["current_uptime"],
            "last_health_check": snapshot["last_health_check"],
            "health_checks_completed": snapshot["health_checks_completed"],
            "health_check_failures": snapshot["health_check_failures"],
            "capability_incidents": snapshot["capability_incidents"],
            "capability_recoveries": snapshot["capability_recoveries"],
            "failed_recoveries": snapshot["failed_recoveries"],
            "escalations": snapshot["escalations"],
            "total_degraded_seconds": snapshot["total_degraded_seconds"],
            "incident_duration": snapshot["incident_duration"],
            "recovery_latency": snapshot["recovery_latency"],
            "task_failures_due_to_runtime_health":
                snapshot["task_failures_due_to_runtime_health"],
            "current_health_state": snapshot["current_health_state"],
            "capability_availability": snapshot["availability_by_capability"],
            "metrics": snapshot["metrics"],
            "slo": snapshot["slo"],
            "slo_state": snapshot["slo"]["state"],
            "current_slo_state": snapshot["slo"]["state"],
            "final_slo_state": (
                snapshot["slo"]["state"]
                if current_lifecycle != "ACTIVE" else None
            ),
            "insufficient_data": sorted(set(insufficient)),
            "measurement_interval": snapshot["current_uptime"],
            "provenance": snapshot["provenance"],
        }

    def reliability_report(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Return a bounded report for the current measurement session."""
        with self._lock:
            if self._finalized_report is not None:
                return copy.deepcopy(self._finalized_report)
            report_now = float(self._clock() if now is None else now)
            return self._build_reliability_report_locked(report_now)

    def finalize_session(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Capture one final reliability report; repeated calls are idempotent."""
        with self._lock:
            if self._finalized_report is not None:
                return copy.deepcopy(self._finalized_report)
            ended_at = float(self._clock() if now is None else now)
            self._account_reliability_locked(ended_at)
            if self._reliability_health_checks == 0:
                lifecycle = "INCOMPLETE"
            elif (self._reliability_active or
                  self._reliability_health_state in {
                      "DEGRADED", "FAILED", "UNCERTAIN", "ESCALATED"
                  }):
                lifecycle = "DEGRADED"
            else:
                lifecycle = "COMPLETED"
            report = self._build_reliability_report_locked(
                ended_at, lifecycle=lifecycle, ended_at=ended_at)
            self._session_lifecycle = lifecycle
            self._session_ended_at = ended_at
            self._finalized_report = copy.deepcopy(report)
            return copy.deepcopy(report)

    def record_health_checkpoint(self, result: Dict[str, Any],
                                 now: Optional[float] = None) -> Dict[str, Any]:
        """Record one supervisor result into bounded session reliability state."""
        with self._lock:
            if self._finalized_report is not None:
                return copy.deepcopy(self._finalized_report)
            checked_at = float(result.get("checked_at",
                                           self._clock() if now is None else now))
            observed_at = float(result.get("completed_at", checked_at))
            escalation_state = str(result.get("escalation_state", "NONE"))
            if escalation_state in {"NOOP_CADENCE", "DEFERRED_ACTIVE_TASK"}:
                return self._reliability_snapshot_locked(observed_at).to_dict()

            self._reliability_last_check = observed_at
            self._reliability_health_checks += 1
            if escalation_state == "UNCERTAIN":
                self._reliability_health_failures += 1
            if bool(result.get("task_interruption")):
                self._reliability_task_failures += 1

            names = [str(name) for name in
                     result.get("capabilities_checked", []) if name]
            self._reliability_capability_names.update(
                names[:RELIABILITY_MAX_CAPABILITIES])
            self._account_reliability_locked(observed_at)

            opened = set()
            for change in result.get("changed_capabilities", []) or []:
                capability = str(change.get("name", ""))[:64]
                state = str(change.get("to", "")).upper()
                reason = str(change.get("reason", ""))[:180]
                if not capability:
                    continue
                if state not in {"READY", "ACTIVE"}:
                    if capability not in self._reliability_active:
                        self._open_reliability_incident_locked(
                            capability, state, reason, checked_at)
                        opened.add(capability)
                    else:
                        incident = self._reliability_active[capability]
                        incident["state"] = state
                        incident["reason"] = reason

            recovery_results = result.get("recovery_result", []) or []
            for recovery in recovery_results:
                capability = str(recovery.get("capability", ""))[:64]
                outcome = str(recovery.get("outcome", "")).upper()
                if capability in self._reliability_active and \
                        outcome in {"RECOVERED", "NO_OP"}:
                    self._close_reliability_incident_locked(
                        capability, observed_at, recovered=True)
                elif capability and outcome not in {"RECOVERED", "NO_OP"}:
                    attempts = int(recovery.get("attempts", 0) or 0)
                    if attempts > 0:
                        self._reliability_failed_recoveries += 1

            for change in result.get("changed_capabilities", []) or []:
                capability = str(change.get("name", ""))[:64]
                if capability and str(change.get("to", "")).upper() in {
                        "READY", "ACTIVE"}:
                    self._close_reliability_incident_locked(
                        capability, observed_at, recovered=False)

            if escalation_state == "UNCERTAIN":
                key = "runtime_health"
                if key not in self._reliability_escalation_keys:
                    self._reliability_escalations += 1
                    self._reliability_escalation_keys.add(key)
            elif escalation_state == "ESCALATED":
                for capability in opened:
                    self._reliability_escalations += 1
                    self._reliability_escalation_keys.add(capability)

            if (escalation_state == "UNCERTAIN" or
                    (escalation_state == "SUPPRESSED" and
                     self._reliability_health_state == "UNCERTAIN")):
                self._reliability_health_state = "UNCERTAIN"
            elif any(inc.get("state") == "FAILED"
                     for inc in self._reliability_active.values()):
                self._reliability_health_state = "FAILED"
            elif self._reliability_active:
                self._reliability_health_state = "DEGRADED"
            elif str(result.get("severity", "")).upper() in {
                    "DEGRADED", "ESCALATED", "FAILED"}:
                self._reliability_health_state = str(result["severity"]).upper()
            else:
                self._reliability_health_state = "HEALTHY"
            return self._reliability_snapshot_locked(observed_at).to_dict()

    def reliability_snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            return self._reliability_snapshot_locked(
                float(self._clock() if now is None else now)).to_dict()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            reliability = self._reliability_snapshot_locked(
                float(self._clock())).to_dict()
            return {
                "turn_id": self._turn_id,
                "turn_text": self._turn_text,
                "goal": self._goal,
                "purpose": self._purpose,
                "task": self._task,
                "step": self._step,
                "step_index": self._step_index,
                "step_total": self._step_total,
                "progress": self._progress,
                "app": self._app,
                "window": self._window,
                "focus": self._focus,
                "screen": self._screen,
                "ocr": self._ocr,
                "ocr_at": self._ocr_at,
                "visual": self._visual,
                "verified": self._verified,
                "verify_note": self._verify_note,
                "recovery": self._recovery,
                "retries": self._retries,
                "correction": self._correction,
                "result": self._result,
                "confidence": self._confidence,
                "provenance": self._provenance,
                "updated": self._updated,
                "reliability": reliability,
                "task_continuity": (
                    self._task_continuity.to_dict()
                    if self._task_continuity is not None else None
                ),
                "action_fence": (
                    copy.deepcopy(self._action_fence)
                    if self._action_fence is not None else None
                ),
                "task_handoff": (
                    copy.deepcopy(self._task_handoff)
                    if self._task_handoff is not None else None
                ),
            }
