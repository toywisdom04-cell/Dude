"""Phase 20 deterministic recovery policy and escalation contract.

This module classifies an already-observed capability condition. It never
repairs, executes, notifies, or owns state; those remain with the existing
supervisor, TaskEngine, CapabilityBus, and user-facing DUDE seams.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

MAX_ATTEMPTS = 2


def _text(value: Any, limit: int = 280) -> str:
    return str(value or "").replace("\n", " ").strip()[:limit]


def _state(status: Optional[Dict[str, Any]]) -> str:
    return _text((status or {}).get("state"), 32).upper() or "UNCERTAIN"


def _reason(status: Optional[Dict[str, Any]]) -> str:
    return _text((status or {}).get("reason"), 180) or "no authoritative reason"


def _decision_value(decision: Any, name: str, default: Any = "") -> Any:
    if isinstance(decision, dict):
        return decision.get(name, default)
    return getattr(decision, name, default)


@dataclass(frozen=True)
class RecoveryPolicyDecision:
    """Explicit policy result for one capability condition."""

    capability: str
    current_state: str
    failure_class: str
    repairability: bool
    risk: str
    automatic_repair_allowed: bool
    max_attempts: int
    escalation_required: bool
    escalation_level: str
    user_action_required: bool
    escalation_message: str
    clear_condition: str
    provenance: str = "RecoveryPolicy+runtime_repair+TaskEngine"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": _text(self.capability, 64),
            "current_state": _text(self.current_state, 32),
            "failure_class": _text(self.failure_class, 40),
            "repairability": bool(self.repairability),
            "risk": _text(self.risk, 32),
            "automatic_repair_allowed": bool(self.automatic_repair_allowed),
            "max_attempts": int(self.max_attempts),
            "escalation_required": bool(self.escalation_required),
            "escalation_level": _text(self.escalation_level, 16),
            "user_action_required": bool(self.user_action_required),
            "escalation_message": _text(self.escalation_message),
            "clear_condition": _text(self.clear_condition),
            "provenance": _text(self.provenance, 120),
        }


def _message(
    capability: str,
    state: str,
    reason: str,
    *,
    recovery_result: Optional[Dict[str, Any]] = None,
    user_action: str = "",
) -> str:
    if recovery_result:
        outcome = _text(recovery_result.get("outcome"), 32) or "FAILED"
        post = recovery_result.get("post_repair_status") or {}
        remaining = _text(post.get("reason"), 120) or _text(
            post.get("state"), 32) or "runtime remains unhealthy"
        return (
            f"{capability} recovery {outcome.lower()}; it remains {state}: "
            f"{remaining}. {user_action or 'No specific user action is known; review runtime status.'}"
        )
    return (
        f"{capability} is {state}: {reason}. "
        f"{user_action or 'No specific user action is known; review runtime status.'}"
    )


def evaluate_recovery_policy(
    capability: str,
    status: Optional[Dict[str, Any]],
    repair_decision: Any = None,
    *,
    task_active: bool = False,
    status_authoritative: bool = True,
    recovery_result: Optional[Dict[str, Any]] = None,
    configured_max_attempts: int = MAX_ATTEMPTS,
) -> RecoveryPolicyDecision:
    """Classify one state without performing speculative recovery."""
    capability = _text(capability, 64)
    state = _state(status)
    reason = _reason(status)
    clear_condition = f"{capability} returns to verified READY/ACTIVE."

    if task_active:
        return RecoveryPolicyDecision(
            capability=capability,
            current_state=state,
            failure_class="TASK_ACTIVE",
            repairability=bool(_decision_value(repair_decision, "repairable", False)),
            risk=_text(_decision_value(repair_decision, "risk"), 32) or "UNKNOWN",
            automatic_repair_allowed=False,
            max_attempts=0,
            escalation_required=False,
            escalation_level="INFO",
            user_action_required=False,
            escalation_message="",
            clear_condition="TaskEngine becomes idle and health can be rechecked.",
        )

    if not status_authoritative or status is None:
        return RecoveryPolicyDecision(
            capability=capability,
            current_state="UNCERTAIN",
            failure_class="STATUS_UNCERTAIN",
            repairability=False,
            risk="UNKNOWN",
            automatic_repair_allowed=False,
            max_attempts=0,
            escalation_required=True,
            escalation_level="HIGH",
            user_action_required=True,
            escalation_message=(
                f"Runtime health for {capability} could not be confirmed; "
                "no speculative repair was attempted. No specific user action "
                "is known; review runtime status."
            ),
            clear_condition=clear_condition,
        )

    if state in {"READY", "ACTIVE"}:
        return RecoveryPolicyDecision(
            capability=capability,
            current_state=state,
            failure_class="HEALTHY",
            repairability=False,
            risk="NONE",
            automatic_repair_allowed=False,
            max_attempts=0,
            escalation_required=False,
            escalation_level="INFO",
            user_action_required=False,
            escalation_message="",
            clear_condition=clear_condition,
        )

    repairable = bool(_decision_value(repair_decision, "repairable", False))
    risk = _text(_decision_value(repair_decision, "risk"), 32) or "UNKNOWN"
    approved = bool(_decision_value(repair_decision, "approved", False))
    recovery_outcome = _text((recovery_result or {}).get("outcome"), 32).upper()
    recovery_succeeded = recovery_outcome in {"RECOVERED", "NO_OP"}

    if approved and risk == "LOW" and not recovery_result:
        return RecoveryPolicyDecision(
            capability=capability,
            current_state=state,
            failure_class="RECOVERABLE",
            repairability=True,
            risk=risk,
            automatic_repair_allowed=True,
            max_attempts=max(1, min(int(configured_max_attempts), MAX_ATTEMPTS)),
            escalation_required=False,
            escalation_level="INFO",
            user_action_required=False,
            escalation_message="",
            clear_condition=clear_condition,
        )

    failure_class = "REPAIR_EXHAUSTED" if recovery_result and not recovery_succeeded \
        else ("NON_REPAIRABLE" if not repairable else "AMBIGUOUS")
    level = "HIGH" if state == "FAILED" or risk in {"HIGH", "UNSAFE_TO_REPAIR"} \
        else "MEDIUM"
    return RecoveryPolicyDecision(
        capability=capability,
        current_state=state,
        failure_class=failure_class,
        repairability=repairable,
        risk=risk,
        automatic_repair_allowed=False,
        max_attempts=0,
        escalation_required=True,
        escalation_level=level,
        user_action_required=True,
        escalation_message=_message(
            capability, state, reason, recovery_result=recovery_result),
        clear_condition=clear_condition,
    )
