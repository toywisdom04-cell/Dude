"""Phase 20 bounded runtime health supervision.

The supervisor has no thread, owner, store, or repair executor. It is called
from an existing DUDE lifecycle checkpoint, reads CapabilityBus truth, applies
the deterministic recovery policy, and delegates approved recovery to
TaskEngine.recover_capability().
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.runtime_recovery_policy import evaluate_recovery_policy
from core.runtime_repair import propose_repair

HEALTHY_STATES = {"READY", "ACTIVE"}
MAX_CHECK_INTERVAL = 300.0
MAX_RECOVERY_ATTEMPTS = 2


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").replace("\n", " ").strip()[:limit]


def _record_view(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _text(record.get("name"), 64),
        "state": _text(record.get("state"), 32).upper(),
        "reason": _text(record.get("reason")),
        "ready": bool(record.get("ready")),
    }


@dataclass
class HealthSupervisionResult:
    """Small explicit result for one supervision checkpoint."""

    checked_at: float
    completed_at: float = 0.0
    capabilities_checked: List[str] = field(default_factory=list)
    changed_capabilities: List[Dict[str, Any]] = field(default_factory=list)
    degraded_capabilities: List[Dict[str, Any]] = field(default_factory=list)
    severity: str = "HEALTHY"
    diagnosis: str = ""
    reason: str = ""
    recovery_attempted: bool = False
    recovery_result: List[Dict[str, Any]] = field(default_factory=list)
    policy_decisions: List[Dict[str, Any]] = field(default_factory=list)
    post_repair_status: Dict[str, Any] = field(default_factory=dict)
    escalation_state: str = "NONE"
    verification_result: Dict[str, Any] = field(default_factory=dict)
    task_continuity: Dict[str, Any] = field(default_factory=dict)
    task_interruption: bool = False
    provenance: str = "RuntimeHealthSupervisor+CapabilityBus+TaskEngine"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked_at": float(self.checked_at),
            "completed_at": float(self.completed_at or self.checked_at),
            "capabilities_checked": list(self.capabilities_checked[:24]),
            "changed_capabilities": list(self.changed_capabilities[:24]),
            "degraded_capabilities": list(self.degraded_capabilities[:24]),
            "severity": _text(self.severity, 32).upper(),
            "diagnosis": _text(self.diagnosis),
            "reason": _text(self.reason),
            "recovery_attempted": bool(self.recovery_attempted),
            "recovery_result": list(self.recovery_result[:24]),
            "policy_decisions": list(self.policy_decisions[:24]),
            "post_repair_status": dict(self.post_repair_status),
            "escalation_state": _text(self.escalation_state, 40).upper(),
            "verification_result": dict(self.verification_result),
            "task_continuity": dict(self.task_continuity),
            "task_interruption": bool(self.task_interruption),
            "provenance": _text(self.provenance, 120),
        }


class RuntimeHealthSupervisor:
    """Lifecycle-checkpoint supervisor; never owns a background thread."""

    def __init__(
        self,
        capability_bus: Any,
        task_engine: Any = None,
        agent_state: Any = None,
        *,
        interval_s: float = 15.0,
        max_attempts: int = MAX_RECOVERY_ATTEMPTS,
        clock: Optional[Callable[[], float]] = None,
        notifier: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.capability_bus = capability_bus
        self.task_engine = task_engine
        self.agent_state = agent_state
        self.notifier = notifier
        self.interval_s = max(1.0, min(float(interval_s), MAX_CHECK_INTERVAL))
        self.max_attempts = max(1, min(int(max_attempts), MAX_RECOVERY_ATTEMPTS))
        self.clock = clock or time.time
        self._last_check = 0.0
        self._previous: Dict[str, str] = {}
        self._previous_reasons: Dict[str, str] = {}
        self._active_escalations: Dict[str, Dict[str, Any]] = {}

    def _task_is_active(self) -> bool:
        engine = self.task_engine
        if engine is None:
            return False
        try:
            if bool(getattr(engine, "runtime_handoff_pending", False)):
                return False
            state = getattr(engine, "state", None)
            name = str(getattr(state, "name", state or "IDLE")).upper()
            return bool(getattr(engine, "_running", False) or
                        name not in {"IDLE", "DONE", "FAILED"})
        except Exception:
            return True

    async def check_active_task(self, *, force: bool = False) -> Dict[str, Any]:
        """Observe active-task health without seizing TaskEngine authority.

        A healthy active task remains untouched. An affected task is held at
        the existing cooperative step boundary; the next bounded health
        checkpoint may use the existing recovery path while that handoff is
        pending.
        """
        now = float(self.clock())
        engine = self.task_engine
        if engine is None:
            return await self.check(force=force)
        if bool(getattr(engine, "runtime_handoff_pending", False)):
            result = await self.check(force=True)
            return await self._finish_task_handoff(result)
        if not self._task_is_active():
            return await self.check(force=force)
        if not force and self._last_check and \
                now - self._last_check < self.interval_s:
            return self._result(
                now,
                severity="INFO",
                diagnosis="active-task health cadence not due",
                reason="read-only active-task health check deferred",
                escalation_state="NOOP_CADENCE",
                verification_result={
                    "success": True,
                    "reason": "cadence not due; no status read or task mutation",
                },
            )
        self._last_check = now
        try:
            response = self.capability_bus.capability_status({})
        except Exception as exc:
            return self._result(
                now,
                severity="INFO",
                diagnosis=f"active-task status unavailable: {exc}",
                reason="runtime status observation deferred while task is active",
                escalation_state="DEFERRED_ACTIVE_TASK",
                verification_result={
                    "success": False,
                    "reason": "active-task status unavailable; no task mutation",
                },
            )
        if not response.get("success"):
            return self._result(
                now,
                severity="INFO",
                diagnosis=str(response.get("error") or
                             "active-task status unavailable"),
                reason="runtime status observation deferred while task is active",
                escalation_state="DEFERRED_ACTIVE_TASK",
                verification_result={
                    "success": False,
                    "reason": "active-task status unavailable; no task mutation",
                },
            )
        records = (response.get("data") or {}).get("capabilities") or []
        current = {
            _text(record.get("name"), 64): record
            for record in records if record.get("name")
        }
        for name, record in current.items():
            self._previous[name] = _text(record.get("state"), 32).upper()
            self._previous_reasons[name] = _text(record.get("reason"), 180)
        affected = []
        degraded = []
        policy_decisions = []
        for name, record in current.items():
            view = _record_view(record)
            state = view["state"]
            ready_when_idle = (
                state == "INACTIVE" and bool(record.get("ready", False)))
            if state in HEALTHY_STATES or ready_when_idle:
                continue
            degraded.append(view)
            if not callable(getattr(engine, "runtime_capability_affects_task",
                                   None)):
                continue
            if not engine.runtime_capability_affects_task(name):
                continue
            policy = evaluate_recovery_policy(
                name, record, None, task_active=True,
                configured_max_attempts=self.max_attempts)
            policy_decisions.append(policy.to_dict())
            handoff = engine.pause_for_runtime_health(
                name, view["reason"] or "runtime capability degraded")
            if handoff:
                affected.append({
                    "name": name,
                    "from": "ACTIVE",
                    "to": state,
                    "reason": view["reason"],
                    "task_continuity": handoff,
                })
        if not affected:
            return self._result(
                now,
                capabilities_checked=sorted(current),
                degraded_capabilities=degraded,
                severity="INFO" if not degraded else "DEGRADED",
                diagnosis="active task observed; no affected capability handoff",
                reason="existing TaskEngine work remains authoritative",
                policy_decisions=policy_decisions,
                escalation_state="DEFERRED_ACTIVE_TASK",
                verification_result={
                    "success": True,
                    "reason": "active task was not interrupted",
                },
            )
        return self._result(
            now,
            capabilities_checked=sorted(current),
            changed_capabilities=affected,
            degraded_capabilities=degraded,
            severity="DEGRADED",
            diagnosis="affected capability captured at TaskEngine boundary",
            reason="task continuity held; existing recovery remains deferred",
            policy_decisions=policy_decisions,
            escalation_state="DEFERRED_ACTIVE_TASK",
            verification_result={
                "success": False,
                "reason": "capability recovery and task safety verification pending",
            },
            task_continuity=(affected[0].get("task_continuity") or {}),
        )

    async def _finish_task_handoff(self, result: Dict[str, Any]
                                   ) -> Dict[str, Any]:
        engine = self.task_engine
        capability = str(getattr(engine, "runtime_handoff_capability", "") or "")
        if not capability:
            return result
        fresh = {}
        try:
            response = self.capability_bus.capability_status({})
            if response.get("success"):
                for record in ((response.get("data") or {}).get("capabilities")
                               or []):
                    if record.get("name") == capability:
                        fresh = record
                        break
        except Exception:
            fresh = {}
        recovery = None
        for item in result.get("recovery_result", []) or []:
            if item.get("capability") == capability:
                recovery = item
                break
        evaluator = getattr(engine, "evaluate_runtime_handoff", None)
        completer = getattr(engine, "complete_runtime_handoff", None)
        if not callable(evaluator) or not callable(completer):
            return result
        evaluation = evaluator(capability, fresh, recovery)
        final = completer(evaluation)
        result["task_continuity"] = final
        result["task_interruption"] = not bool(
            final.get("safe_to_resume", False))
        result["verification_result"] = {
            "success": bool(final.get("safe_to_resume", False)),
            "method": "task_continuity_evaluation",
            "reason": final.get("reason", ""),
        }
        return result

    def _notify(self, event: Dict[str, Any]) -> None:
        if not callable(self.notifier):
            return
        try:
            self.notifier(dict(event))
        except Exception:
            # A user-facing seam must never break health supervision truth.
            pass

    def _clear_incident(self, capability: str, reason: str) -> bool:
        previous = self._active_escalations.pop(capability, None)
        if previous is None:
            return False
        message = f"{capability} recovered and is verified READY/ACTIVE."
        self._notify({
            "kind": "cleared",
            "capability": capability,
            "state": "READY",
            "message": message,
            "provenance": "RuntimeHealthSupervisor+RecoveryPolicy",
        })
        setter = getattr(self.agent_state, "set_recovery", None)
        if callable(setter):
            try:
                setter(f"health:CLEARED:{capability}; {reason}"[:240], 0)
            except Exception:
                pass
        return True

    def _escalate(self, capability: str, state: str,
                  policy: Any) -> bool:
        message = str(getattr(policy, "escalation_message", "") or
                      f"{capability} requires human attention.")
        previous = self._active_escalations.get(capability)
        meaningful = (previous is None or
                      previous.get("state") != state or
                      previous.get("message") != message)
        self._active_escalations[capability] = {
            "state": state,
            "reason": message,
            "message": message,
        }
        if meaningful:
            self._notify({
                "kind": "escalation",
                "capability": capability,
                "state": state,
                "level": str(getattr(policy, "escalation_level", "MEDIUM")),
                "message": message,
                "provenance": str(getattr(policy, "provenance", "")),
            })
        return meaningful

    def _record_session(self, result: HealthSupervisionResult) -> None:
        if self.agent_state is None:
            return
        setter = getattr(self.agent_state, "set_recovery", None)
        if not callable(setter) or result.escalation_state in {
            "NOOP_CADENCE", "DEFERRED_ACTIVE_TASK", "SUPPRESSED", "NONE", "CLEARED"
        }:
            return
        names = ",".join(
            item.get("name", "") for item in result.degraded_capabilities[:3]
        )
        note = (
            f"health:{result.severity}:{result.escalation_state};"
            f"{names}; {result.reason}"
        )
        try:
            setter(note[:240], 0)
        except Exception:
            pass

    def _result(self, now: float, *, completed_at: Optional[float] = None,
                **kwargs: Any) -> Dict[str, Any]:
        result = HealthSupervisionResult(
            checked_at=now,
            completed_at=float(now if completed_at is None else completed_at),
            **kwargs
        )
        self._record_session(result)
        result_dict = result.to_dict()
        recorder = getattr(self.agent_state, "record_health_checkpoint", None)
        if callable(recorder):
            try:
                recorder(result_dict, now=now)
            except Exception:
                # Reliability metrics are evidence, not health truth.
                pass
        return result_dict

    def _uncertain_result(self, now: float, diagnosis: str) -> Dict[str, Any]:
        policy = evaluate_recovery_policy(
            "runtime_health", None, None,
            status_authoritative=False,
            configured_max_attempts=self.max_attempts,
        )
        meaningful = self._escalate("runtime_health", "UNCERTAIN", policy)
        result = self._result(
            now,
            severity="FAILED",
            diagnosis=diagnosis,
            reason="supervision uncertainty; runtime is not assumed healthy",
            policy_decisions=[policy.to_dict()],
            escalation_state="UNCERTAIN" if meaningful else "SUPPRESSED",
            verification_result={
                "success": False,
                "reason": "authoritative capability status unavailable",
            },
        )
        return result

    async def check(self, *, force: bool = False) -> Dict[str, Any]:
        """Check once at an existing lifecycle checkpoint."""
        now = float(self.clock())
        if not force and self._last_check and \
                now - self._last_check < self.interval_s:
            return self._result(
                now,
                severity="HEALTHY",
                diagnosis="supervision cadence not due",
                reason="cheap no-op before next bounded health checkpoint",
                escalation_state="NOOP_CADENCE",
                verification_result={
                    "success": True,
                    "reason": "cadence not due; no status read or repair",
                },
            )
        self._last_check = now

        if self._task_is_active():
            policy = evaluate_recovery_policy(
                "runtime_health", None, None, task_active=True,
                configured_max_attempts=self.max_attempts,
            )
            return self._result(
                now,
                severity="INFO",
                diagnosis="TaskEngine owns an active user task",
                reason="supervision deferred to avoid seizing execution authority",
                policy_decisions=[policy.to_dict()],
                escalation_state="DEFERRED_ACTIVE_TASK",
                verification_result={
                    "success": True,
                    "reason": "supervision deferred while user task is active",
                },
            )

        try:
            response = self.capability_bus.capability_status({})
        except Exception as exc:
            return self._uncertain_result(
                now, f"CapabilityBus status collection raised: {exc}")
        if not response.get("success"):
            return self._uncertain_result(
                now, str(response.get("error") or "CapabilityBus status failed"))

        records = (response.get("data") or {}).get("capabilities") or []
        if not records:
            return self._uncertain_result(
                now, "CapabilityBus returned no capability records")

        current = {
            _text(record.get("name"), 64): record
            for record in records if record.get("name")
        }
        checked = sorted(current)
        changed: List[Dict[str, Any]] = []
        degraded: List[Dict[str, Any]] = []
        recovery_results: List[Dict[str, Any]] = []
        policy_decisions: List[Dict[str, Any]] = []
        recovery_attempted = False
        recovered = False
        escalated = False
        suppressed = False
        post_repair_status: Dict[str, Any] = {}
        transition_reason = ""

        handoff_capability = str(
            getattr(self.task_engine, "runtime_handoff_capability", "") or "")
        handoff_pending = bool(
            getattr(self.task_engine, "runtime_handoff_pending", False))
        for name in checked:
            record = current[name]
            state = _text(record.get("state"), 32).upper()
            previous = self._previous.get(name)
            previous_reason = self._previous_reasons.get(name, "")
            view = _record_view(record)
            if state not in HEALTHY_STATES:
                degraded.append(view)
            if previous is None:
                self._previous[name] = state
                self._previous_reasons[name] = view["reason"]
                continue
            if state == previous and not (
                    handoff_pending and name == handoff_capability):
                if state not in HEALTHY_STATES and \
                        name in self._active_escalations and \
                        view["reason"] != previous_reason:
                    changed.append({
                        "name": name,
                        "from": state,
                        "to": state,
                        "reason": view["reason"],
                        "reason_changed": True,
                    })
                    transition_reason = transition_reason or view["reason"]
                    self._previous_reasons[name] = view["reason"]
                    decision = propose_repair(name, record, self.capability_bus)
                    policy = evaluate_recovery_policy(
                        name, record, decision,
                        recovery_result={
                            "outcome": "UNREPAIRED",
                            "post_repair_status": record,
                        },
                        configured_max_attempts=self.max_attempts,
                    )
                    policy_decisions.append(policy.to_dict())
                    self._escalate(name, state, policy)
                    escalated = True
                elif state not in HEALTHY_STATES and \
                        name in self._active_escalations:
                    escalated = True
                    suppressed = True
                continue

            changed.append({
                "name": name,
                "from": previous,
                "to": state,
                "reason": view["reason"],
            })
            transition_reason = transition_reason or view["reason"]
            self._previous[name] = state
            self._previous_reasons[name] = view["reason"]

            if state in HEALTHY_STATES:
                self._clear_incident(name, view["reason"] or "verified READY/ACTIVE")
                recovered = True
                continue

            if name in self._active_escalations and \
                    self._active_escalations[name].get("state") == state:
                escalated = True
                suppressed = True
                continue

            decision = propose_repair(name, record, self.capability_bus)
            policy = evaluate_recovery_policy(
                name, record, decision,
                configured_max_attempts=self.max_attempts,
            )
            policy_decisions.append(policy.to_dict())
            if policy.automatic_repair_allowed and self.task_engine is not None:
                recovery_attempted = True
                recovery = await self.task_engine.recover_capability(
                    name,
                    problem=view["reason"],
                    capability_bus=self.capability_bus,
                    max_attempts=self.max_attempts,
                )
                recovery_results.append(recovery)
                post_repair_status = recovery.get("post_repair_status") or {}
                post_state = _text(
                    post_repair_status.get("state"), 32).upper()
                post_policy = evaluate_recovery_policy(
                    name, post_repair_status, decision,
                    recovery_result=recovery,
                    configured_max_attempts=self.max_attempts,
                )
                policy_decisions.append(post_policy.to_dict())
                self._previous[name] = post_state or state
                self._previous_reasons[name] = _text(
                    post_repair_status.get("reason"), 180)
                if recovery.get("outcome") == "RECOVERED":
                    self._clear_incident(name, view["reason"] or "verified READY/ACTIVE")
                    degraded[:] = [item for item in degraded
                                   if item.get("name") != name]
                    recovered = True
                else:
                    self._escalate(name, post_state or state, post_policy)
                    escalated = True
            elif policy.automatic_repair_allowed:
                policy = evaluate_recovery_policy(
                    name, record, decision,
                    recovery_result={
                        "outcome": "UNREPAIRED",
                        "post_repair_status": record,
                    },
                    configured_max_attempts=self.max_attempts,
                )
                policy_decisions[-1] = policy.to_dict()
                post_repair_status = record
                recovery_results.append({
                    "capability": name,
                    "outcome": "UNREPAIRED",
                    "repair": decision.to_dict(),
                    "policy": policy.to_dict(),
                    "attempts": 0,
                    "provenance": policy.provenance,
                })
                self._escalate(name, state, policy)
                escalated = True
            elif policy.escalation_required:
                post_repair_status = record
                recovery_results.append({
                    "capability": name,
                    "outcome": "UNREPAIRED",
                    "repair": decision.to_dict(),
                    "policy": policy.to_dict(),
                    "attempts": 0,
                    "provenance": policy.provenance,
                })
                self._escalate(name, state, policy)
                escalated = True
            else:
                # DEFER is handled before status collection; retain a truthful
                # policy result if a future caller supplies one here.
                policy_decisions.append(policy.to_dict())

        if any(
            _text((current.get(name) or {}).get("state"), 32).upper() == "FAILED"
            for name in checked
        ):
            severity = "FAILED"
            escalation_state = (
                "SUPPRESSED"
                if suppressed and not changed and not recovery_attempted
                else "ESCALATED"
            )
        elif escalated:
            severity = "ESCALATED"
            escalation_state = (
                "SUPPRESSED"
                if suppressed and not changed and not recovery_attempted
                else "ESCALATED"
            )
        elif degraded:
            severity = "DEGRADED"
            escalation_state = "NONE"
        elif recovered and changed:
            severity = "HEALTHY"
            escalation_state = "CLEARED"
        else:
            severity = "HEALTHY"
            escalation_state = "NONE"

        verification_success = not escalated and not recovery_results and not degraded
        if recovery_results:
            verification_success = all(
                item.get("verification_result", {}).get("success", False)
                for item in recovery_results
            ) and not escalated
        return self._result(
            now,
            completed_at=float(self.clock()),
            capabilities_checked=checked,
            changed_capabilities=changed,
            degraded_capabilities=degraded,
            severity=severity,
            diagnosis=(transition_reason or
                       ("health state changed" if changed else
                        "identical unhealthy state already escalated" if suppressed
                        else "runtime healthy")),
            reason=(transition_reason or
                    ("health state changed" if changed else
                     "identical unhealthy state already escalated" if suppressed
                     else "no meaningful change")),
            recovery_attempted=recovery_attempted,
            recovery_result=recovery_results,
            policy_decisions=policy_decisions,
            post_repair_status=post_repair_status,
            escalation_state=escalation_state,
            verification_result={
                "success": bool(verification_success),
                "method": "fresh_capability_status",
                "reason": (
                    "supervision verified current runtime truth"
                    if verification_success else
                    "one or more conditions require escalation"
                ),
            },
        )
