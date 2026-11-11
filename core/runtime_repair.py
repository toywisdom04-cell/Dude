"""Phase 20 bounded capability-repair decisions.

This module is a pure, non-persistent decision layer over CapabilityBus
status and existing owner operations. TaskEngine remains the only component
that invokes those operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

MAX_TEXT = 280


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "").replace("\n", " ").strip()[:limit]


def _bounded(value: Any, depth: int = 0) -> Any:
    """Keep repair reports small even when a test owner returns rich data."""
    if depth >= 3:
        return _text(value)
    if isinstance(value, dict):
        return {
            _text(key, 64): _bounded(item, depth + 1)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in list(value)[:12]]
    if isinstance(value, str):
        return _text(value)
    return value


@dataclass
class CapabilityRepairDecision:
    """Explicit diagnosis-to-verification contract for one capability."""

    capability: str
    problem: str
    diagnosis: str
    repairable: bool
    proposed_repair: str
    risk: str
    approved: bool
    execution_result: Dict[str, Any] = field(default_factory=dict)
    post_repair_status: Dict[str, Any] = field(default_factory=dict)
    verification_result: Dict[str, Any] = field(default_factory=dict)
    provenance: str = "TaskEngine+CapabilityBus"
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": _text(self.capability, 64),
            "problem": _text(self.problem),
            "diagnosis": _text(self.diagnosis),
            "repairable": bool(self.repairable),
            "proposed_repair": _text(self.proposed_repair, 120),
            "risk": _text(self.risk, 32),
            "approved": bool(self.approved),
            "execution_result": _bounded(self.execution_result),
            "post_repair_status": _bounded(self.post_repair_status),
            "verification_result": _bounded(self.verification_result),
            "provenance": _text(self.provenance, 120),
            "attempts": int(self.attempts),
        }


def _decision(
    capability: str,
    status: Optional[Dict[str, Any]],
    *,
    repairable: bool,
    proposed_repair: str,
    risk: str,
    diagnosis: str,
) -> CapabilityRepairDecision:
    state = _text((status or {}).get("state", "UNAVAILABLE"), 32)
    reason = _text((status or {}).get("reason", "runtime status unavailable"))
    problem = f"{state}: {reason}" if status else "capability status unavailable"
    return CapabilityRepairDecision(
        capability=capability,
        problem=problem,
        diagnosis=diagnosis,
        repairable=repairable,
        proposed_repair=proposed_repair,
        risk=risk,
        approved=repairable and risk == "LOW",
    )


def propose_repair(
    capability: str,
    status: Optional[Dict[str, Any]],
    capability_bus: Any,
) -> CapabilityRepairDecision:
    """Propose only deterministic, low-risk repairs of existing owners."""
    capability = _text(capability, 64)
    if not status:
        return _decision(
            capability, status, repairable=False,
            proposed_repair="none", risk="UNSAFE_TO_REPAIR",
            diagnosis="CapabilityBus did not return a status record",
        )

    state = _text(status.get("state"), 32).upper()
    if state in ("READY", "ACTIVE"):
        return _decision(
            capability, status, repairable=False,
            proposed_repair="none", risk="NONE",
            diagnosis="capability is already healthy; no repair is needed",
        )

    if capability == "stt" and state == "FAILED":
        owner = getattr(capability_bus, "ear", None)
        if owner is not None and callable(getattr(owner, "warm_start", None)):
            return _decision(
                capability, status, repairable=True,
                proposed_repair="ear.warm_start", risk="LOW",
                diagnosis="existing Ear owner exposes bounded STT warm-start",
            )

    if capability == "perception" and state in ("INACTIVE", "FAILED"):
        owner = getattr(capability_bus, "perception_service", None)
        if owner is not None and callable(getattr(owner, "start", None)):
            return _decision(
                capability, status, repairable=True,
                proposed_repair="perception_service.start", risk="LOW",
                diagnosis="existing PerceptionService.start() is idempotent",
            )

    if capability == "uia" and state == "DEGRADED":
        owner = getattr(capability_bus, "screentree", None)
        if owner is not None and callable(getattr(owner, "refresh_sync", None)):
            return _decision(
                capability, status, repairable=True,
                proposed_repair="screentree.refresh_sync", risk="LOW",
                diagnosis="existing ScreenMap.refresh_sync() rebuilds UIA data",
            )

    return _decision(
        capability, status, repairable=False,
        proposed_repair="none", risk="UNSAFE_TO_REPAIR",
        diagnosis="no approved low-risk owner repair matches this status",
    )
