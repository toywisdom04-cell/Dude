"""DUDE capability bus: one general gateway to existing capabilities.

Routes structured requests to the subsystem that already owns each
capability. Adapters only — no duplicated implementations, no fake
capabilities. Every response carries success/data/evidence/provenance
plus error/reason/confidence, and distinguishes unavailable (no backend)
from failed (attempted, errored) from empty (attempted, nothing found).

Lazy imports throughout: this module must never create import cycles.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _result(capability: str, success: bool, data: Any = None,
            evidence: str = "", provenance: str = "",
            error: str = "", confidence: float = 0.0) -> Dict[str, Any]:
    return {
        "success": success,
        "capability": capability,
        "data": data,
        "evidence": evidence,
        "provenance": provenance,
        "error": error,
        "confidence": confidence,
    }


class CapabilityBus:
    """Gateway. Context carries live refs: memory, observer, brain,
    screentree, ask_user, perception, action_executor, verification,
    recovery, tools, and the existing runtime owners used by the
    Phase 20 capability status layer. Set once at boot via attach()."""

    def __init__(self):
        self._ctx: Dict[str, Any] = {}

    def attach(self, **refs) -> None:
        self._ctx.update({k: v for k, v in refs.items() if v is not None})

    @property
    def perception(self):
        """Access to PerceptionEngine for live observation."""
        return self._ctx.get("perception")

    @property
    def action_executor(self):
        return self._ctx.get("action_executor")

    @property
    def verification(self):
        # Boot attaches the live owner as `verification`; accept the old
        # spelling only as a compatibility alias for existing test harnesses.
        return (self._ctx.get("verification") or
                self._ctx.get("verification_engine"))

    @property
    def recovery(self):
        # Boot attaches the live owner as `recovery`; accept the old spelling
        # only as a compatibility alias for existing test harnesses.
        return (self._ctx.get("recovery") or
                self._ctx.get("recovery_engine"))

    @property
    def ear(self):
        return self._ctx.get("ear")

    @property
    def voice(self):
        return self._ctx.get("voice")

    @property
    def perception_service(self):
        return self._ctx.get("perception_service")

    @property
    def procedure_store(self):
        return self._ctx.get("procedure_store")

    @property
    def intelligence_router(self):
        return self._ctx.get("intelligence_router")

    @property
    def task_engine(self):
        return self._ctx.get("task_engine")

    @property
    def skill_pack_registry(self):
        return self._ctx.get("skill_pack_registry")

    @property
    def context_primer(self):
        return self._ctx.get("context_primer")

    @property
    def memory(self):
        return self._ctx.get("memory")

    @property
    def observer(self):
        return self._ctx.get("observer")

    @property
    def brain(self):
        return self._ctx.get("brain")

    @property
    def screentree(self):
        return self._ctx.get("screentree")

    @property
    def ask_user(self):
        return self._ctx.get("ask_user")

    @property
    def visual_state_bus(self):
        """Phase 14: current visual state (complements live refs)."""
        return self._ctx.get("visual_state_bus")

    @property
    def spatial_workspace(self):
        """Phase 15: spatial show-me presentation capability."""
        return self._ctx.get("spatial_workspace")

    @property
    def hand_tracker(self):
        """Phase 16: hand/gesture sensor capability."""
        return self._ctx.get("hand_tracker")

    @property
    def spatial_hand_interaction(self):
        """Phase 16: virtual-object hit testing and contextual pointer."""
        tracker = self._ctx.get("hand_tracker")
        return getattr(tracker, "spatial_layer", None) if tracker else None

    @property
    def shared_workspace(self):
        """Phase 19: current multimodal shared workspace (read-only)."""
        try:
            from core.orchestrator.shared_workspace import last_workspace
            return last_workspace()
        except Exception:
            return None

    def request(self, capability: str,
                payload: Optional[Dict[str, Any]] = None,
                timeout_s: float = 30.0) -> Dict[str, Any]:
        payload = payload or {}
        handler = {
            "SCREEN_STATE": self._screen_state,
            "UIA": self._uia,
            "SCREEN_CAPTURE": self._screen_capture,
            "OCR": self._ocr,
            "VISION": self._vision,
            "MEMORY_RECALL": self._memory_recall,
            "HUMAN_MEMORY": self._human_memory,
            "JOB_MEMORY": self._job_memory,
            "SHARED_WORKSPACE": self._shared_workspace,
            "PROCEDURES": self._procedures,
            "CAPABILITY_DISCOVERY": self._discovery,
            "TOOLS": self._tools,
            "CAPABILITY_STATUS": self._capability_status,
            "PROVIDER_STATUS": self._provider_status,
            "HAND_STATUS": self._hand_status,
            "SPATIAL_HAND_STATUS": self._spatial_hand_status,
        }.get((capability or "").upper())
        if handler is None:
            return _result(capability, False,
                           error=f"unknown capability: {capability}")
        try:
            return handler(payload)
        except Exception as e:
            return _result(capability, False,
                           error=f"{type(e).__name__}: {e}")

    # ---------------- handlers (adapters over owners) ----------------

    def _hand_status(self, payload):
        """Expose the existing HandTracker truth through CapabilityBus."""
        tracker = self._ctx.get("hand_tracker")
        if tracker is None:
            return _result("HAND_STATUS", False,
                           error="HandTracker not attached")
        try:
            return _result("HAND_STATUS", True, data=tracker.status(),
                           evidence="HandTracker.status()",
                           provenance="CapabilityBus+HandTracker")
        except Exception as exc:
            return _result("HAND_STATUS", False,
                           error=f"HandTracker status failed: {exc}",
                           provenance="CapabilityBus+HandTracker")

    def _spatial_hand_status(self, payload):
        layer = self.spatial_hand_interaction
        if layer is None:
            return _result("SPATIAL_HAND_STATUS", False,
                           error="spatial hand layer not attached")
        try:
            return _result(
                "SPATIAL_HAND_STATUS", True, data=layer.snapshot(),
                evidence="SpatialInteractionLayer.snapshot()",
                provenance="CapabilityBus+SpatialInteractionLayer")
        except Exception as exc:
            return _result("SPATIAL_HAND_STATUS", False,
                           error=f"spatial hand status failed: {exc}",
                           provenance="CapabilityBus+SpatialInteractionLayer")

    def capability_status(self, payload: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
        """Return a fresh, bounded snapshot from the current owner refs."""
        return self.request("CAPABILITY_STATUS", payload or {})

    def _capability_status(self, payload):
        try:
            from core.runtime_capability_status import (
                collect_runtime_capability_status,
            )
            data = collect_runtime_capability_status(self._ctx)
            return _result(
                "CAPABILITY_STATUS", True, data=data,
                evidence="fresh owner-derived runtime status",
                provenance="runtime_capability_status")
        except Exception as e:
            return _result(
                "CAPABILITY_STATUS", False,
                error=f"status collection failed: {type(e).__name__}: {e}",
                provenance="runtime_capability_status")

    def _provider_status(self, payload):
        brain = self._ctx.get("brain")
        if brain is None:
            return _result("PROVIDER_STATUS", False,
                           error="brain not attached")
        try:
            health = brain.provider_health()
        except Exception as exc:
            return _result("PROVIDER_STATUS", False,
                           error=f"provider status failed: {exc}")
        return _result("PROVIDER_STATUS", True, data=health,
                       evidence="live Brain provider health snapshot",
                       provenance="core.brain.Brain", confidence=1.0)

    def _screen_state(self, payload):
        observer = self._ctx.get("observer")
        if observer is None:
            return _result("SCREEN_STATE", False, error="no observer")
        try:
            snap = observer.refresh_snapshot() if payload.get("fresh") \
                else observer.current_screen()
        except Exception as e:
            return _result("SCREEN_STATE", False,
                           error=f"snapshot failed: {e}")
        if not isinstance(snap, dict):
            return _result("SCREEN_STATE", False, error="no snapshot")
        app = (snap.get("app") or "").strip()
        data = {
            "app": app,
            "window": (snap.get("title") or "").strip(),
            "focus": (snap.get("focused_name") or "").strip(),
            "dialog": (snap.get("dialog_kind") or "").strip(),
            "description": str(snap.get("description") or "").strip(),
            "ocr": " ".join(str(snap.get("ocr") or "").split()),
            "fresh": bool(payload.get("fresh")),
        }
        if not app or app == "unknown":
            return _result("SCREEN_STATE", False, data=data,
                           error="no application identity",
                           provenance="observer")
        return _result("SCREEN_STATE", True, data=data,
                       evidence="observer snapshot",
                       provenance="observer", confidence=0.8)

    def _uia(self, payload):
        tree = self._ctx.get("screentree")
        if tree is None:
            return _result("UIA", False, error="no UI map")
        try:
            view = tree.current_view(
                limit=int(payload.get("limit", 18) or 18))
        except Exception as e:
            return _result("UIA", False, error=f"UI map failed: {e}")
        if not view:
            return _result("UIA", False, error="empty UI map",
                           provenance="screentree")
        return _result("UIA", True, data={"view": view},
                       evidence="live UI Automation map",
                       provenance="screentree", confidence=0.9)

    def _screen_capture(self, payload):
        try:
            from core.tools import _capture_screen_composite
            img, _ = _capture_screen_composite()
        except Exception as e:
            return _result("SCREEN_CAPTURE", False,
                           error=f"capture failed: {e}")
        if img is None:
            return _result("SCREEN_CAPTURE", False, error="no image")
        return _result("SCREEN_CAPTURE", True,
                       data={"size": getattr(img, "size", None)},
                       evidence="fresh composite capture",
                       provenance="tools", confidence=1.0)

    def _ocr(self, payload):
        observer = self._ctx.get("observer")
        text = ""
        provenance = ""
        if observer is not None:
            try:
                snap = observer.refresh_snapshot() if payload.get("fresh") \
                    else observer.current_screen()
                if isinstance(snap, dict):
                    text = " ".join(str(snap.get("ocr") or "").split())
                    provenance = "observer"
            except Exception as e:
                return _result("OCR", False, error=f"observer OCR failed: {e}")
        if not text:
            try:
                from core.tools import read_screen_text
                import inspect as _insp
                if "memory" in _insp.signature(read_screen_text).parameters:
                    out = read_screen_text(payload.get("region", ""),
                                           self._ctx.get("memory"))
                else:
                    out = read_screen_text(payload.get("region", ""))
                if isinstance(out, str) and out.strip():
                    text = " ".join(out.split())
                    provenance = "read_screen_text tool"
            except Exception:
                pass
        if not text:
            return _result("OCR", False, error="no text observed",
                           provenance=provenance or "observer")
        return _result("OCR", True, data={"text": text[:1200]},
                       evidence="tesseract OCR", provenance=provenance,
                       confidence=0.7)

    def _vision(self, payload):
        brain = self._ctx.get("brain")
        if brain is None:
            return _result("VISION", False, error="no brain")
        try:
            has_vision = brain.has_vision()
        except Exception:
            has_vision = False
        if not has_vision:
            return _result("VISION", False, error="no vision backend",
                           provenance="brain")
        try:
            import base64
            import io
            from PIL import Image
            from core.tools import _capture_screen_composite
            img, _ = _capture_screen_composite()
            if img is None:
                return _result("VISION", False, error="no image")
            im = img.convert("RGB")
            im.thumbnail((1280, 1280))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=60)
            b64 = base64.b64encode(buf.getvalue()).decode()
            out = brain.vision_analyze(
                b64, payload.get("prompt") or "Describe this screen.")
            if not (out or "").strip():
                return _result("VISION", False, error="empty analysis",
                               provenance="brain")
            return _result("VISION", True, data={"analysis": out.strip()},
                           evidence="vision model", provenance="brain",
                           confidence=0.6)
        except Exception as e:
            return _result("VISION", False, error=f"vision failed: {e}")

    @staticmethod
    def _memory_fact_records(facts):
        """Adapt the existing Memory string API to the existing bundle shape.

        Memory remains the single owner and continues to return list[str].
        The CapabilityBus boundary exposes the dict-like facts already
        consumed by MemoryBundle/IntelligenceRouter; this is not a second
        memory store or representation owner.
        """
        records = []
        for fact in facts or []:
            if isinstance(fact, dict):
                content = (fact.get("content") or fact.get("fact") or
                           str(fact))
            else:
                content = str(fact)
            content = str(content).strip()
            if not content:
                continue
            content = content[:2000]
            records.append({"content": content, "fact": content})
        return records

    def _memory_recall(self, payload):
        memory = self._ctx.get("memory")
        if memory is None:
            return _result("MEMORY_RECALL", False, error="no memory")
        try:
            facts = memory.recall_facts(payload.get("query", ""),
                                        limit=int(payload.get("limit", 4) or 4))
        except Exception as e:
            return _result("MEMORY_RECALL", False, error=f"recall failed: {e}")
        records = self._memory_fact_records(facts)
        if not records:
            return _result("MEMORY_RECALL", False, data=[],
                           error="nothing relevant", provenance="memory")
        return _result("MEMORY_RECALL", True, data=records,
                       evidence="semantic recall", provenance="memory",
                       confidence=0.6)

    def _human_memory(self, payload):
        """Phase 18: human semantic memory (read/remember/profile)."""
        memory = self._ctx.get("memory")
        if memory is None:
            return _result("HUMAN_MEMORY", False, error="no memory")
        try:
            from core import human_memory as _hm
            op = (payload.get("op") or "recall").lower()
            if op == "remember":
                ok = _hm.remember_about_human(
                    memory,
                    fact=payload.get("fact", ""),
                    category=payload.get("category", "personal"),
                    subject=payload.get("subject", ""),
                    confidence=float(payload.get("confidence", 0.7) or 0.7),
                    source=payload.get("source", "user_stated"))
                return _result("HUMAN_MEMORY", bool(ok),
                               data={"stored": bool(ok)},
                               evidence="semantic fact",
                               provenance="memory.facts",
                               confidence=0.8 if ok else 0.2)
            if op == "profile":
                cached = _hm.cached_profile(memory)
                prof = (cached if cached and not payload.get("rebuild")
                        else _hm.build_profile(memory))
                return _result("HUMAN_MEMORY", True,
                               data={"profile": prof,
                                     "summary":
                                         _hm.profile_summary(prof)},
                               evidence="human profile",
                               provenance="memory.session_state",
                               confidence=0.7)
            rows = _hm.recall_about_human(
                memory, query=payload.get("query", ""),
                limit=int(payload.get("limit", 6) or 6),
                include_superseded=bool(payload.get("include_superseded")))
            if not rows:
                return _result("HUMAN_MEMORY", False, data=[],
                               error="nothing remembered",
                               provenance="memory.facts")
            return _result("HUMAN_MEMORY", True, data=rows,
                           evidence="current human facts",
                           provenance="memory.facts", confidence=0.7)
        except Exception as e:
            return _result("HUMAN_MEMORY", False, error=f"failed: {e}")

    def _shared_workspace(self, payload):
        """Phase 19: the current shared multimodal workspace."""
        try:
            from core.orchestrator.shared_workspace import last_workspace
            ws = last_workspace()
            if ws is None:
                return _result("SHARED_WORKSPACE", False,
                               error="no workspace composed yet",
                               provenance="shared_workspace")
            op = (payload.get("op") or "status").lower()
            if op == "describe":
                return _result("SHARED_WORKSPACE", True,
                               data={"describe": ws.describe()},
                               evidence="current workspace",
                               provenance="shared_workspace", confidence=0.8)
            if op == "correlation":
                name = payload.get("name", "hand->visual")
                rel = ws.correlation(name)
                if rel is None:
                    return _result("SHARED_WORKSPACE", False,
                                   error="no such correlation",
                                   provenance="shared_workspace")
                return _result("SHARED_WORKSPACE", True, data=rel,
                               evidence="workspace correlation",
                               provenance="shared_workspace",
                               confidence=0.8 if rel.get("valid") else 0.3)
            return _result("SHARED_WORKSPACE", True,
                           data={"workspace": ws.to_dict(),
                                 "status": ws.status()},
                           evidence="current workspace",
                           provenance="shared_workspace", confidence=0.7)
        except Exception as e:
            return _result("SHARED_WORKSPACE", False, error=f"failed: {e}")

    def _job_memory(self, payload):
        memory = self._ctx.get("memory")
        if memory is None:
            return _result("JOB_MEMORY", False, error="no memory")
        try:
            from core import job_memory as _jm
            op = (payload.get("op") or "find").lower()
            if op == "get":
                card = _jm.get_job(memory, payload.get("name", ""))
                if not card:
                    return _result("JOB_MEMORY", False, error="no such job",
                                   provenance="job_memory")
                return _result("JOB_MEMORY", True, data=card,
                               evidence="stored job card",
                               provenance="job_memory", confidence=0.9)
            cards = _jm.find_jobs(memory, payload.get("query", ""),
                                  limit=int(payload.get("limit", 3) or 3))
            if not cards:
                return _result("JOB_MEMORY", False, data=[],
                               error="no matching job",
                               provenance="job_memory")
            return _result("JOB_MEMORY", True, data=cards,
                           evidence="keyword job match",
                           provenance="job_memory", confidence=0.7)
        except Exception as e:
            return _result("JOB_MEMORY", False, error=f"job lookup failed: {e}")

    def _procedures(self, payload):
        try:
            from core.orchestrator import get_procedure_store
            store = get_procedure_store()
            procs = store.find_by_goal(payload.get("goal", ""),
                                       min_confidence=float(
                                           payload.get("min_confidence", 0.5)
                                           or 0.5))
        except Exception as e:
            return _result("PROCEDURES", False, error=f"lookup failed: {e}")
        if not procs:
            return _result("PROCEDURES", False, data=[],
                           error="no verified procedure",
                           provenance="procedure_store")
        return _result("PROCEDURES", True, data=procs,
                       evidence="verified procedure match",
                       provenance="procedure_store", confidence=0.7)

    def _discovery(self, payload):
        try:
            from core.orchestrator import get_skill_registry
            reg = get_skill_registry()
            found = reg.find_by_capability(payload.get("capability", ""))
        except Exception as e:
            return _result("CAPABILITY_DISCOVERY", False,
                           error=f"registry failed: {e}")
        if not found:
            return _result("CAPABILITY_DISCOVERY", False, data=[],
                           error="no skill provides it",
                           provenance="skill_registry")
        return _result("CAPABILITY_DISCOVERY", True,
                       data=[{"name": getattr(s, "name", ""),
                              "confidence": 0.8} for s in found],
                       evidence="skill registry", provenance="skill_registry",
                       confidence=0.7)

    def _tools(self, payload):
        memory = self._ctx.get("memory")
        ask = self._ctx.get("ask_user")
        name = (payload.get("name") or "").strip()
        if not name:
            return _result("TOOLS", False, error="no tool name")
        if memory is None or ask is None:
            return _result("TOOLS", False,
                           error="tool execution not wired (memory/ask_user)")
        try:
            import json as _json
            from core.tools import execute_tool
            out = execute_tool(name, _json.dumps(payload.get("args", {})),
                               memory, ask)
        except Exception as e:
            return _result("TOOLS", False, error=f"execution failed: {e}")
        if isinstance(out, str) and out.startswith("ERR"):
            return _result("TOOLS", False, data=out, error=out[:200],
                           provenance="tools")
        return _result("TOOLS", True, data=out, evidence="tool executed",
                       provenance="tools", confidence=0.8)
