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
    screentree, ask_user, perception, action_executor, verification_engine,
    recovery_engine, tools. Set once at boot via attach()."""

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
        return self._ctx.get("verification_engine")

    @property
    def recovery(self):
        return self._ctx.get("recovery_engine")

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
            "JOB_MEMORY": self._job_memory,
            "PROCEDURES": self._procedures,
            "CAPABILITY_DISCOVERY": self._discovery,
            "TOOLS": self._tools,
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

    def _memory_recall(self, payload):
        memory = self._ctx.get("memory")
        if memory is None:
            return _result("MEMORY_RECALL", False, error="no memory")
        try:
            facts = memory.recall_facts(payload.get("query", ""),
                                        limit=int(payload.get("limit", 4) or 4))
        except Exception as e:
            return _result("MEMORY_RECALL", False, error=f"recall failed: {e}")
        if not facts:
            return _result("MEMORY_RECALL", False, data=[],
                           error="nothing relevant", provenance="memory")
        return _result("MEMORY_RECALL", True, data=facts,
                       evidence="semantic recall", provenance="memory",
                       confidence=0.6)

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
