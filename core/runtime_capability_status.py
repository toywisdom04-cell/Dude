"""Phase 20: bounded runtime capability truth.

This module is a read-only status layer over the existing capability owners.
It does not create providers, stores, engines, planners, or executors. Every
record is derived from the owner object currently attached to CapabilityBus or
from a bounded, non-mutating owner probe.
"""
from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

MAX_REASON = 180
MAX_EVIDENCE = 280
MAX_PROVENANCE = 96
MAX_CAPABILITIES = 24

CAPABILITY_ORDER = (
    "tesseract",
    "ocr",
    "perception",
    "uia",
    "stt",
    "tts",
    "hand",
    "memory",
    "procedures",
    "skill_packs",
    "computer_tools",
    "verification",
    "recovery",
    "brain_routing",
    "vision",
    "shared_workspace",
)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit]


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _cfg(config: Any, section: str, key: str, default: Any = None) -> Any:
    try:
        if config is not None and hasattr(config, "get"):
            return config.get(section, key, default=default)
    except Exception:
        pass
    return default


def _thread_alive(owner: Any, attr: str) -> bool:
    try:
        thread = getattr(owner, attr, None)
        return bool(thread and thread.is_alive())
    except Exception:
        return False


def _call(owner: Any, attr: str, default: Any = None) -> Any:
    try:
        value = getattr(owner, attr, None)
        return value() if callable(value) else (value if value is not None else default)
    except Exception:
        return default


def _state(
    *,
    present: bool,
    wired: bool,
    active: bool,
    ready: bool,
    degraded: bool = False,
    failed: bool = False,
    unavailable: bool = False,
    inactive: bool = False,
) -> str:
    if failed:
        return "FAILED"
    if unavailable:
        return "UNAVAILABLE"
    if degraded:
        return "DEGRADED"
    if active and ready:
        return "READY"
    if active and wired:
        return "ACTIVE"
    if inactive:
        return "INACTIVE"
    if wired:
        return "WIRED"
    if present:
        return "PRESENT"
    return "UNAVAILABLE"


@dataclass(frozen=True)
class CapabilityStatus:
    """One bounded, non-persistent capability status record."""

    name: str
    present: bool = False
    wired: bool = False
    active: bool = False
    ready: bool = False
    degraded: bool = False
    failed: bool = False
    unavailable: bool = False
    inactive: bool = False
    state: str = "UNAVAILABLE"
    reason: str = ""
    evidence: str = ""
    provenance: str = ""
    checked_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": _clip(self.name, 64),
            "present": bool(self.present),
            "wired": bool(self.wired),
            "active": bool(self.active),
            "ready": bool(self.ready),
            "degraded": bool(self.degraded),
            "failed": bool(self.failed),
            "unavailable": bool(self.unavailable),
            "inactive": bool(self.inactive),
            "state": self.state,
            "reason": _clip(self.reason, MAX_REASON),
            "evidence": _clip(self.evidence, MAX_EVIDENCE),
            "provenance": _clip(self.provenance, MAX_PROVENANCE),
            "checked_at": float(self.checked_at),
        }


class RuntimeCapabilityStatus:
    """Build a fresh bounded status snapshot from CapabilityBus references."""

    def __init__(self, refs: Mapping[str, Any], config: Any = None,
                 clock: Optional[Callable[[], float]] = None):
        self.refs = refs or {}
        self.config = config
        self.clock = clock or time.time

    def _record(self, name: str, **kwargs: Any) -> Dict[str, Any]:
        checked_at = float(kwargs.pop("checked_at", self.clock()))
        return CapabilityStatus(
            name=name,
            checked_at=checked_at,
            state=_state(
                present=bool(kwargs.get("present", False)),
                wired=bool(kwargs.get("wired", False)),
                active=bool(kwargs.get("active", False)),
                ready=bool(kwargs.get("ready", False)),
                degraded=bool(kwargs.get("degraded", False)),
                failed=bool(kwargs.get("failed", False)),
                unavailable=bool(kwargs.get("unavailable", False)),
                inactive=bool(kwargs.get("inactive", False)),
            ),
            **kwargs,
        ).to_dict()

    def _missing_owner(self, name: str, present: bool, provenance: str,
                       reason: str = "runtime owner not attached") -> Dict[str, Any]:
        return self._record(
            name,
            present=present,
            wired=False,
            active=False,
            ready=False,
            inactive=present,
            unavailable=not present,
            reason=reason,
            evidence="owner reference absent from current CapabilityBus snapshot",
            provenance=provenance,
        )

    def _probe_tesseract(self) -> Dict[str, Any]:
        present = _module_present("core.ocr") and _module_present("pytesseract")
        provenance = "core.ocr"
        if not present:
            return self._missing_owner("tesseract", False, provenance,
                                       "Tesseract wrapper or pytesseract unavailable")
        try:
            from core.ocr import is_tesseract_available
            available = bool(is_tesseract_available())
        except Exception as exc:
            return self._record(
                "tesseract", present=True, wired=False, active=False,
                ready=False, failed=True, reason=f"probe failed: {exc}",
                evidence="core.ocr availability probe raised",
                provenance=provenance)
        observer = self.refs.get("observer")
        wired = observer is not None
        ocr_at = float(getattr(observer, "_ocr_at", 0.0) or 0.0) if observer else 0.0
        active = bool(observer is not None and
                      getattr(observer, "enabled", False) and
                      _thread_alive(observer, "_thread") and ocr_at > 0.0)
        ready = bool(available and wired)
        if not available:
            state = dict(present=True, wired=wired, active=active, ready=False,
                         unavailable=True, reason="Tesseract executable unavailable",
                         evidence="is_tesseract_available() returned false",
                         provenance=provenance)
        elif not wired:
            state = dict(present=True, wired=False, active=False, ready=False,
                         inactive=True, reason="Tesseract present; OCR owner not attached",
                         evidence="CapabilityBus has no observer reference",
                         provenance=provenance)
        else:
            state = dict(present=True, wired=True, active=active, ready=ready,
                         inactive=not active,
                         reason=("Tesseract ready; awaiting current OCR sample"
                                 if not active else "Tesseract available and OCR owner active"),
                         evidence=f"available={available}; observer_active={active}",
                         provenance=provenance)
        return self._record("tesseract", **state)

    def _probe_ocr(self) -> Dict[str, Any]:
        present = _module_present("core.ocr") and _module_present("core.observer")
        observer = self.refs.get("observer")
        if not present:
            return self._missing_owner("ocr", False, "core.observer+core.ocr",
                                       "OCR owner module unavailable")
        if observer is None:
            return self._missing_owner("ocr", True, "core.observer",
                                       "OCR owner not attached")
        enabled = bool(getattr(observer, "enabled", False))
        active = bool(enabled and _thread_alive(observer, "_thread"))
        text = str(getattr(observer, "_ocr", "") or "")
        ocr_at = float(getattr(observer, "_ocr_at", 0.0) or 0.0)
        ready = bool(active)
        return self._record(
            "ocr", present=True, wired=True, active=active, ready=ready,
            inactive=not active,
            reason=("observer OCR worker stopped" if not active
                    else "observer OCR worker active"),
            evidence=f"enabled={enabled}; ocr_chars={len(text)}; ocr_at={ocr_at:.3f}",
            provenance="core.observer",
        )

    def _probe_perception(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.perception")
        owner = self.refs.get("perception")
        service = self.refs.get("perception_service")
        if not present:
            return self._missing_owner("perception", False,
                                       "core.orchestrator.perception",
                                       "PerceptionEngine unavailable")
        if owner is None:
            return self._missing_owner("perception", True,
                                       "core.orchestrator.perception",
                                       "PerceptionEngine not attached")
        wired = True
        active = bool(service is not None and _thread_alive(service, "_fast_thread"))
        if service is None:
            cache = getattr(owner, "_cache", None)
            captured = float(getattr(cache, "captured_at", 0.0) or 0.0)
            active = captured > 0.0
        callable_observe = callable(getattr(owner, "observe", None))
        ready = bool(active and callable_observe)
        degraded = bool(owner is not None and not callable_observe)
        return self._record(
            "perception", present=True, wired=wired, active=active, ready=ready,
            degraded=degraded, inactive=not active,
            reason=("perception service inactive" if not active
                    else "perception service active"),
            evidence=f"service_attached={service is not None}; observe_callable={callable_observe}",
            provenance="PerceptionService+PerceptionEngine",
        )

    def _probe_uia(self) -> Dict[str, Any]:
        present = _module_present("core.screentree")
        tree = self.refs.get("screentree")
        if not present:
            return self._missing_owner("uia", False, "core.screentree",
                                       "ScreenMap unavailable")
        if tree is None:
            return self._missing_owner("uia", True, "core.screentree",
                                       "ScreenMap not attached")
        try:
            available = bool(tree.available())
        except Exception:
            available = False
        active = bool(available and _thread_alive(tree, "_thread"))
        try:
            rows = len(tree.rows)
        except Exception:
            rows = 0
        ready = bool(active and callable(getattr(tree, "current_view", None)))
        degraded = bool(active and rows == 0)
        unavailable = not available
        return self._record(
            "uia", present=True, wired=True, active=active, ready=ready,
            degraded=degraded, unavailable=unavailable,
            inactive=not active and not unavailable,
            reason=("UI Automation unavailable" if unavailable
                    else "UIA thread inactive" if not active
                    else "UIA active with no current controls" if degraded
                    else "UIA active"),
            evidence=f"available={available}; rows={rows}; thread_alive={_thread_alive(tree, '_thread')}",
            provenance="core.screentree.ScreenMap",
        )

    def _probe_stt(self) -> Dict[str, Any]:
        present = _module_present("core.ear") and _module_present("faster_whisper")
        ear = self.refs.get("ear")
        if not present:
            return self._missing_owner("stt", False, "core.ear",
                                       "STT dependency unavailable")
        if ear is None:
            return self._missing_owner("stt", True, "core.ear",
                                       "Ear owner not attached")
        proc = getattr(ear, "_stt_proc", None)
        proc_alive = False
        if proc is not None:
            try:
                proc_alive = proc.poll() is None
            except Exception:
                proc_alive = False
        mic_alive = _thread_alive(ear, "_mic_thread")
        stt_alive = _thread_alive(ear, "_stt_thread")
        active = bool(getattr(ear, "_running", False) and mic_alive and
                      stt_alive and proc_alive)
        ready = active
        failed = bool(proc is not None and not proc_alive)
        return self._record(
            "stt", present=True, wired=True, active=active, ready=ready,
            failed=failed, inactive=not active and not failed,
            reason=("STT worker exited" if failed
                    else "STT threads/worker active" if active
                    else "STT owner attached but not active"),
            evidence=f"mic_alive={mic_alive}; stt_alive={stt_alive}; worker_alive={proc_alive}",
            provenance="core.ear.Ear",
        )

    def _probe_tts(self) -> Dict[str, Any]:
        present = (_module_present("core.voice") and
                   _module_present("edge_tts") and
                   _module_present("pygame"))
        voice = self.refs.get("voice")
        if not present:
            return self._missing_owner("tts", False, "core.voice",
                                       "TTS dependency unavailable")
        if voice is None:
            return self._missing_owner("tts", True, "core.voice",
                                       "Voice owner not attached")
        active = _thread_alive(voice, "_thread")
        silent = bool(getattr(voice, "silent", False))
        ready = bool(active and not silent)
        return self._record(
            "tts", present=True, wired=True, active=active, ready=ready,
            inactive=not active or silent,
            reason=("Voice quiet mode" if silent
                    else "Voice worker active" if active
                    else "Voice owner attached but worker inactive"),
            evidence=f"thread_alive={active}; silent={silent}; mixer_ready={bool(getattr(voice, '_mixer_ready', False))}",
            provenance="core.voice.Voice",
        )

    def _probe_hand(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.hand_tracking")
        tracker = self.refs.get("hand_tracker")
        if not present:
            return self._missing_owner("hand", False,
                                       "core.orchestrator.hand_tracking",
                                       "Hand tracking module unavailable")
        if tracker is None:
            return self._missing_owner("hand", True,
                                       "core.orchestrator.hand_tracking",
                                       "HandTracker not attached")
        try:
            raw = tracker.status()
            state = str(raw.get("state", "")).upper()
            reason = str(raw.get("reason", "")).strip()
        except Exception as exc:
            return self._record(
                "hand", present=True, wired=True, active=False, ready=False,
                failed=True, reason=f"owner status failed: {exc}",
                evidence="HandTracker.status() raised", provenance="HandTracker.status")
        if state == "AVAILABLE":
            return self._record(
                "hand", present=True, wired=True, active=True, ready=True,
                reason=reason or "hand stream available",
                evidence="HandTracker.status() == AVAILABLE",
                provenance="HandTracker.status")
        if state == "DEGRADED":
            return self._record(
                "hand", present=True, wired=True, active=True, ready=False,
                degraded=True, reason=reason or "hand stream degraded",
                evidence="HandTracker.status() == DEGRADED",
                provenance="HandTracker.status")
        inactive = reason in ("camera stopped", "starting up, no frames yet") or \
            "camera stopped" in reason
        unavailable = not inactive
        return self._record(
            "hand", present=True, wired=True, active=False, ready=False,
            unavailable=unavailable, inactive=inactive,
            reason=reason or "hand owner not active",
            evidence=f"HandTracker.status() == {state or 'UNKNOWN'}",
            provenance="HandTracker.status")

    def _probe_memory(self) -> Dict[str, Any]:
        present = _module_present("core.memory")
        memory = self.refs.get("memory")
        if not present:
            return self._missing_owner("memory", False, "core.memory",
                                       "Memory module unavailable")
        if memory is None:
            return self._missing_owner("memory", True, "core.memory",
                                       "Memory owner not attached")
        conn = getattr(memory, "conn", None)
        methods_ok = callable(getattr(memory, "recall_facts", None)) and \
            callable(getattr(memory, "remember_fact", None))
        active = conn is not None
        ready = bool(active and methods_ok)
        return self._record(
            "memory", present=True, wired=True, active=active, ready=ready,
            inactive=not active,
            reason=("memory connection closed" if not active
                    else "memory owner ready"),
            evidence=f"connection_open={active}; api_methods={methods_ok}",
            provenance="core.memory.Memory",
        )

    def _probe_procedures(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.procedure_store")
        store = self.refs.get("procedure_store")
        if not present:
            return self._missing_owner("procedures", False,
                                       "ProcedureStore", "Procedure module unavailable")
        if store is None:
            return self._missing_owner("procedures", True, "ProcedureStore",
                                       "ProcedureStore not attached")
        try:
            stats = store.get_stats() if callable(getattr(store, "get_stats", None)) else {}
            active = True
            ready = bool(stats is not None)
            evidence = (f"procedures={stats.get('total_procedures', '?')}; "
                        f"successes={stats.get('total_successes', '?')}; "
                        f"failures={stats.get('total_failures', '?')}")
        except Exception as exc:
            active = True
            ready = False
            return self._record(
                "procedures", present=True, wired=True, active=active,
                ready=False, failed=True, reason=f"procedure store probe failed: {exc}",
                evidence="ProcedureStore.get_stats() raised",
                provenance="ProcedureStore.get_stats")
        return self._record(
            "procedures", present=True, wired=True, active=active, ready=ready,
            reason="procedure store reachable; no verified procedure is not an infrastructure failure",
            evidence=evidence, provenance="ProcedureStore.get_stats")

    def _probe_skill_packs(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.skill_packs")
        registry = self.refs.get("skill_pack_registry")
        if not present:
            return self._missing_owner("skill_packs", False, "SkillPackRegistry",
                                       "Skill pack module unavailable")
        if registry is None:
            return self._missing_owner("skill_packs", True, "SkillPackRegistry",
                                       "SkillPackRegistry not attached")
        try:
            count = int(registry.count())
            errors = list(getattr(registry, "load_errors", []) or [])
            loaded_at = float(getattr(registry, "loaded_at", 0.0) or 0.0)
            active = loaded_at > 0.0
            ready = bool(active and count > 0 and not errors)
            return self._record(
                "skill_packs", present=True, wired=True, active=active,
                ready=ready, degraded=bool(errors),
                inactive=not active or (count == 0 and not errors),
                reason=("skill pack load errors" if errors
                        else "no enabled skill packs loaded" if count == 0
                        else "skill packs loaded"),
                evidence=f"count={count}; load_errors={len(errors)}",
                provenance="SkillPackRegistry")
        except Exception as exc:
            return self._record(
                "skill_packs", present=True, wired=True, active=False,
                ready=False, failed=True, reason=f"skill pack probe failed: {exc}",
                evidence="SkillPackRegistry status raised",
                provenance="SkillPackRegistry")

    def _probe_computer_tools(self) -> Dict[str, Any]:
        present = _module_present("core.tools")
        memory = self.refs.get("memory")
        ask_user = self.refs.get("ask_user")
        if not present:
            return self._missing_owner("computer_tools", False, "core.tools",
                                       "Computer tool registry unavailable")
        try:
            import core.tools as tools
            registry = getattr(tools, "REGISTRY", {}) or {}
            platform = getattr(tools, "PLATFORM", None)
            wired = bool(memory is not None and ask_user is not None and platform is not None)
            active = bool(registry and platform is not None)
            open_url_missing = bool("open_url" in registry and
                                    platform is not None and
                                    not hasattr(platform, "open_url"))
            ready = bool(active and wired and not open_url_missing)
            return self._record(
                "computer_tools", present=True, wired=wired, active=active,
                ready=ready, degraded=bool(open_url_missing and wired),
                inactive=not wired or not active,
                reason=("tool adapter limitation: open_url unavailable"
                        if open_url_missing and wired
                        else "computer tool owner not attached" if not wired
                        else "computer tool registry active" if active
                        else "computer tools not active"),
                evidence=f"registry={len(registry)}; platform={type(platform).__name__ if platform else 'none'}; open_url_missing={open_url_missing}",
                provenance="core.tools.REGISTRY+CapabilityBus.TOOLS")
        except Exception as exc:
            return self._record(
                "computer_tools", present=True, wired=False, active=False,
                ready=False, failed=True, reason=f"tool registry probe failed: {exc}",
                evidence="core.tools import/status raised", provenance="core.tools")

    def _task_state_name(self) -> str:
        task_engine = self.refs.get("task_engine")
        state = getattr(task_engine, "state", None)
        return getattr(state, "name", str(state or "")).upper()

    def _probe_verification(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.verification")
        owner = self.refs.get("verification")
        if not present:
            return self._missing_owner("verification", False, "VerificationEngine",
                                       "VerificationEngine unavailable")
        if owner is None:
            return self._missing_owner("verification", True, "VerificationEngine",
                                       "VerificationEngine not attached")
        callable_verify = callable(getattr(owner, "verify", None))
        perception = getattr(owner, "perception", None)
        active = self._task_state_name() == "VERIFYING"
        ready = bool(callable_verify and perception is not None)
        return self._record(
            "verification", present=True, wired=True, active=active, ready=ready,
            inactive=not active,
            reason=("verification active" if active
                    else "verification owner ready but idle"),
            evidence=f"verify_callable={callable_verify}; perception_attached={perception is not None}; task_state={self._task_state_name()}",
            provenance="VerificationEngine")

    def _probe_recovery(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.recovery")
        owner = self.refs.get("recovery")
        if not present:
            return self._missing_owner("recovery", False, "RecoveryEngine",
                                       "RecoveryEngine unavailable")
        if owner is None:
            return self._missing_owner("recovery", True, "RecoveryEngine",
                                       "RecoveryEngine not attached")
        callable_decide = callable(getattr(owner, "decide", None))
        max_retries = int(getattr(owner, "max_retries", 0) or 0)
        active = self._task_state_name() == "RECOVERING"
        ready = bool(callable_decide and max_retries > 0)
        return self._record(
            "recovery", present=True, wired=True, active=active, ready=ready,
            inactive=not active,
            reason=("recovery active" if active
                    else "recovery owner ready but idle"),
            evidence=f"decide_callable={callable_decide}; max_retries={max_retries}; task_state={self._task_state_name()}",
            provenance="RecoveryEngine")

    def _probe_brain(self) -> Dict[str, Any]:
        present = _module_present("core.brain")
        brain = self.refs.get("brain")
        router = self.refs.get("intelligence_router")
        if not present:
            return self._missing_owner("brain_routing", False, "core.brain",
                                       "Brain module unavailable")
        if brain is None:
            return self._missing_owner("brain_routing", True, "core.brain",
                                       "Brain owner not attached")
        providers = list(getattr(brain, "providers", []) or [])
        active_provider = getattr(brain, "active_provider", None)
        active = active_provider is not None
        route_callable = callable(getattr(router, "route", None)) if router else False
        model_decomposition_ready = True
        if self.refs.get("task_engine") is not None:
            try:
                import core.orchestrator.task_engine as task_engine_module
                model_decomposition_ready = hasattr(
                    task_engine_module, "GenerateRequest")
            except Exception:
                model_decomposition_ready = False
        ready = bool(providers and (callable(getattr(brain, "chat", None)) or
                                    callable(getattr(brain, "think", None))) and
                     route_callable and model_decomposition_ready)
        degraded = bool(providers and (not route_callable or
                                       not model_decomposition_ready))
        reason = ("no enabled model providers" if not providers
                  else "model provider configured; no active request yet"
                  if not active else "model routing active")
        if not model_decomposition_ready:
            reason += "; TaskEngine model decomposition contract unavailable"
        return self._record(
            "brain_routing", present=True, wired=True, active=active, ready=ready,
            degraded=degraded, unavailable=not providers,
            inactive=not active and bool(providers),
            reason=reason,
            evidence=(f"providers={len(providers)}; active_provider="
                       f"{getattr(active_provider, 'name', '')}; router_attached="
                       f"{router is not None}; model_decomposition_ready="
                       f"{model_decomposition_ready}"),
            provenance="core.brain.Brain+IntelligenceRouter+TaskEngine")

    def _probe_vision(self) -> Dict[str, Any]:
        present = _module_present("core.brain")
        brain = self.refs.get("brain")
        if not present:
            return self._missing_owner("vision", False, "core.brain",
                                       "Vision component unavailable")
        if brain is None:
            return self._missing_owner("vision", True, "core.brain",
                                       "Brain owner not attached")
        try:
            has_vision = bool(brain.has_vision())
        except Exception as exc:
            return self._record(
                "vision", present=True, wired=True, active=False, ready=False,
                failed=True, reason=f"vision health probe failed: {exc}",
                evidence="Brain.has_vision() raised", provenance="Brain.has_vision")
        active_provider = getattr(brain, "active_provider", None)
        active = bool(has_vision and active_provider is not None and
                      getattr(active_provider, "vision", False))
        ready = bool(has_vision and callable(getattr(brain, "vision_analyze", None)))
        return self._record(
            "vision", present=True, wired=True, active=active, ready=ready,
            unavailable=not has_vision, inactive=has_vision and not active,
            reason=("no enabled vision provider" if not has_vision
                    else "vision provider configured" if not active
                    else "vision provider active"),
            evidence=f"has_vision={has_vision}; active_provider_vision={getattr(active_provider, 'vision', False)}",
            provenance="Brain.has_vision")

    def _probe_shared_workspace(self) -> Dict[str, Any]:
        present = _module_present("core.orchestrator.shared_workspace")
        if not present:
            return self._missing_owner("shared_workspace", False,
                                       "SharedWorkspace", "SharedWorkspace unavailable")
        try:
            from core.orchestrator.shared_workspace import last_workspace
            workspace = last_workspace()
        except Exception as exc:
            return self._record(
                "shared_workspace", present=True, wired=False, active=False,
                ready=False, failed=True, reason=f"workspace status failed: {exc}",
                evidence="last_workspace() raised", provenance="SharedWorkspace")
        if workspace is None:
            return self._missing_owner("shared_workspace", True, "SharedWorkspace",
                                       "no workspace composed yet")
        try:
            raw_status = workspace.status()
            state = str(raw_status.get("shared_workspace") or
                        raw_status.get("state") or "").upper()
        except Exception:
            state = ""
        ready = state == "AVAILABLE"
        return self._record(
            "shared_workspace", present=True, wired=True, active=True,
            ready=ready, degraded=bool(state and not ready), inactive=not state,
            reason=state.lower() or "workspace present",
            evidence=f"state={state or 'unknown'}",
            provenance="SharedWorkspace.status")

    def snapshot(self) -> Dict[str, Any]:
        probes = (
            self._probe_tesseract, self._probe_ocr, self._probe_perception,
            self._probe_uia, self._probe_stt, self._probe_tts, self._probe_hand,
            self._probe_memory, self._probe_procedures, self._probe_skill_packs,
            self._probe_computer_tools, self._probe_verification,
            self._probe_recovery, self._probe_brain, self._probe_vision,
            self._probe_shared_workspace,
        )
        records = []
        for probe in probes[:MAX_CAPABILITIES]:
            try:
                record = probe()
            except Exception as exc:
                record = self._record(
                    CAPABILITY_ORDER[len(records)], present=True,
                    wired=False, active=False, ready=False, failed=True,
                    reason=f"status probe failed: {exc}",
                    evidence="runtime status probe raised", provenance="RuntimeCapabilityStatus")
            records.append(record)
        states = {r["name"]: r["state"] for r in records}
        return {
            "version": "phase20-runtime-status-v1",
            "checked_at": self.clock(),
            "capabilities": records,
            "states": states,
            "phase17": self._phase17(records),
            "phase19": self._phase19(records),
        }

    def _phase17(self, records: list) -> Dict[str, Any]:
        item = next((r for r in records if r["name"] == "skill_packs"), {})
        evidence = item.get("evidence", "")
        count = None
        for part in evidence.split(";"):
            if part.strip().startswith("count="):
                try:
                    count = int(part.split("=", 1)[1].strip())
                except Exception:
                    count = None
        return {
            "skill_pack_state": item.get("state", "UNAVAILABLE"),
            "installed_pack_count": count or 0,
            "bounded_retrieval": "max 5 facts / 2 jobs / 2 procedures / 3 packs per turn",
        }

    def _phase19(self, records: list) -> Dict[str, Any]:
        item = next((r for r in records if r["name"] == "shared_workspace"), {})
        return {
            "shared_workspace_state": item.get("state", "UNAVAILABLE"),
            "shared_workspace_evidence": item.get("evidence", ""),
        }


def collect_runtime_capability_status(refs: Mapping[str, Any], config: Any = None,
                                      clock: Optional[Callable[[], float]] = None
                                      ) -> Dict[str, Any]:
    """Public bounded collector used by CapabilityBus."""
    return RuntimeCapabilityStatus(refs, config=config, clock=clock).snapshot()
