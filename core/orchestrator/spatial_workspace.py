"""Phase 15: spatial "show me" workspace.

Pipeline (strict order, one authoritative state):

    Perception -> VisualStateBus -> SpatialWorkspace -> Overlay

The workspace NEVER polls the screen: every target resolves from the
bus's current state. Coordinates are never memorized — a target is
valid only while the bus still holds the state it was resolved from.

No gesture/hand tracking here (Phase 16). No cloud, no model calls.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

_HIGHLIGHT = "#00E5FF"
_HIGHLIGHT_OUTER = "#003338"
_LABEL_BG = "#0B2B30"
_LABEL_FG = "#D9FBFF"
_BORDER = 4
_DEFAULT_SHOW_SECONDS = 15.0


def _norm(text: Any) -> str:
    try:
        s = str(text or "").lower()
    except Exception:
        return ""
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _rect_ok(rect: Any) -> bool:
    try:
        return (isinstance(rect, (tuple, list)) and len(rect) == 4
                and int(rect[2]) > 0 and int(rect[3]) > 0)
    except Exception:
        return False


@dataclass
class SpatialTarget:
    """One located on-screen referent with provenance."""
    name: str = ""
    kind: str = ""  # "uia" | "ocr" | "window" | "focus"
    rect: tuple = ()  # (x, y, w, h) screen coordinates from evidence
    window: tuple = ()  # (app, title) the target was seen in
    provenance: str = ""  # e.g. "uia:Save" / "ocr:hello" / "window:active"
    state_key: tuple = ()  # bus key the target was resolved from
    captured_at: float = field(default_factory=time.time)
    label: str = ""  # short human label for the overlay
    reason: str = ""  # why this target is relevant (optional)

    def age_seconds(self) -> float:
        try:
            return max(0.0, time.time() - self.captured_at)
        except Exception:
            return 0.0

    def is_live(self, bus, ttl_seconds: float = 5.0) -> bool:
        """True only while the bus still holds the resolving state."""
        try:
            if bus is None:
                return False
            cur = bus.get()
            if cur is None:
                return False
            if tuple(cur.key()) != tuple(self.state_key):
                return False
            return self.age_seconds() <= ttl_seconds
        except Exception:
            return False


def resolve_named(expression: str, state) -> Optional[SpatialTarget]:
    """Locate a named element in a visual state (UIA first, OCR fallback).

    The expression arrives from the cognitive layer (goal, plan target,
    answer referent) — this function never sees user phrasing. Returns
    None (never a guessed box) when nothing matches with real geometry.
    """
    try:
        want = _norm(expression)
        if not want or state is None:
            return None
        window = (getattr(state, "active_app", "unknown"),
                  getattr(state, "window_title", ""))
        key = tuple(state.key()) if hasattr(state, "key") else window

        def _score(have: str) -> int:
            if not have or not want:
                return 0
            if have == want:
                return 3
            if have.startswith(want) or want.startswith(have):
                return 2
            if want in have or have in want:
                return 1
            return 0

        names = list(getattr(state, "control_names", "") or [])
        rects = list(getattr(state, "control_rects", "") or [])
        best, best_score = None, 0
        for i, raw in enumerate(names):
            have = _norm(raw)
            sc = _score(have)
            if sc <= best_score:
                continue
            rect = rects[i] if i < len(rects) else ()
            if not _rect_ok(rect):
                continue
            best, best_score = (raw, tuple(rect)), sc
        if best is not None:
            return SpatialTarget(
                name=best[0], kind="uia", rect=best[1], window=window,
                provenance=f"uia:{best[0][:60]}", state_key=key,
                label=str(best[0])[:40])

        for item in (getattr(state, "ocr_items", "") or []):
            try:
                text, rect = item[0], item[1]
            except Exception:
                continue
            if _score(_norm(text)) <= 0 or not _rect_ok(rect):
                continue
            return SpatialTarget(
                name=str(text)[:60], kind="ocr", rect=tuple(rect),
                window=window, provenance=f"ocr:{str(text)[:60]}",
                state_key=key, label=str(text)[:40])
        return None
    except Exception as e:
        log.warning(f"resolve_named failed: {e}")
        return None


def resolve_focus(state) -> Optional[SpatialTarget]:
    """Locate the current attention focus: focused control, else window."""
    try:
        if state is None:
            return None
        window = (getattr(state, "active_app", "unknown"),
                  getattr(state, "window_title", ""))
        key = tuple(state.key()) if hasattr(state, "key") else window
        fname = getattr(state, "focused_name", "") or ""
        frect = getattr(state, "focused_rect", "") or ()
        if fname and _rect_ok(frect):
            return SpatialTarget(
                name=fname, kind="focus", rect=tuple(frect), window=window,
                provenance=f"focus:{fname[:60]}", state_key=key,
                label=str(fname)[:40])
        bounds = getattr(state, "window_bounds", "") or ()
        if _rect_ok(bounds):
            return SpatialTarget(
                name=window[1] or window[0], kind="window",
                rect=tuple(bounds), window=window,
                provenance="window:active", state_key=key,
                label=str(window[1] or window[0])[:40])
        return None
    except Exception as e:
        log.warning(f"resolve_focus failed: {e}")
        return None


class _MemoryOverlay:
    """Headless overlay backend (tests): records calls, draws nothing."""

    def __init__(self):
        self.shown: list = []
        self.clears = 0
        self.up = True
        self.last_error = ""

    def show(self, targets) -> bool:
        self.shown.append(list(targets))
        return True

    def clear(self) -> None:
        self.clears += 1

    def shutdown(self) -> None:
        self.up = False


class SpatialOverlay:
    """Non-destructive topmost highlight overlay (own thread).

    Click-through, never activated (no focus steal), never touches the
    perception stack: any overlay failure stays inside this object.
    """

    def __init__(self, backend: str = "tk",
                 show_seconds: float = _DEFAULT_SHOW_SECONDS):
        self.show_seconds = show_seconds
        self.last_error = ""
        self._impl = _MemoryOverlay() if backend != "tk" else None
        self._use_tk = backend == "tk"
        if self._use_tk:
            try:
                from . import spatial_tk as _tk
                self._impl = _tk.TkOverlay(show_seconds=show_seconds)
            except Exception as e:
                self.last_error = f"tk backend unavailable: {e}"
                log.warning(self.last_error)
                self._impl = _MemoryOverlay()
                self._use_tk = False

    def show(self, targets) -> bool:
        try:
            if not targets:
                return False
            ok = bool(self._impl.show(list(targets)))
            if not ok:
                self.last_error = getattr(self._impl, "last_error", "show failed")
            return ok
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning(f"overlay show failed: {e}")
            return False

    def clear(self) -> None:
        try:
            self._impl.clear()
        except Exception as e:
            self.last_error = str(e)[:200]

    def shutdown(self) -> None:
        try:
            self._impl.shutdown()
        except Exception:
            pass

    @property
    def is_up(self) -> bool:
        try:
            return bool(getattr(self._impl, "up", False))
        except Exception:
            return False

    @property
    def ready(self) -> bool:
        """Usable for future shows (window may not exist yet)."""
        try:
            if hasattr(self._impl, "ready"):
                return bool(self._impl.ready)
            return bool(getattr(self._impl, "up", False))
        except Exception:
            return False

    @property
    def last_drawn(self) -> list:
        """Geometry rasterized by the most recent draw."""
        try:
            return list(getattr(self._impl, "last_drawn", []) or [])
        except Exception:
            return []

    def inspect(self) -> dict:
        """Live overlay diagnostics (canvas items, mapping, record)."""
        try:
            fn = getattr(self._impl, "inspect", None)
            if callable(fn):
                return dict(fn() or {})
            return {}
        except Exception as e:
            return {"error": str(e)[:200]}


class SpatialWorkspace:
    """Bus-driven presentation controller (the capability)."""

    def __init__(self, bus=None, overlay=None, ttl_seconds: float = 5.0):
        self.bus = bus
        self.overlay = overlay or SpatialOverlay(backend="memory")
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._shown: list[SpatialTarget] = []
        self._shown_key: tuple = ()
        # Follow entries: what each shown target was resolved FROM, so a
        # moved window re-resolves to new geometry (never blind reuse).
        self._entries: list[dict] = []
        self.last_error = ""
        self._unsub = None
        try:
            if self.bus is not None and hasattr(self.bus, "subscribe"):
                self._unsub = self.bus.subscribe(self._on_bus_update)
        except Exception as e:
            log.warning(f"workspace bus subscribe failed: {e}")

    def _on_bus_update(self, state) -> None:
        """Follow moved targets; drop ones that no longer resolve.

        A new bus state never inherits old coordinates blindly: every
        shown entry re-resolves against the fresh state. Changed
        geometry updates the overlay; vanished elements clear it.
        """
        try:
            with self._lock:
                if not self._entries:
                    return
                entries = list(self._entries)
            try:
                fresh = self.bus.get_fresh()
            except Exception:
                fresh = None
            if fresh is None:
                with self._lock:
                    self._entries = []
                    self._shown = []
                    self._shown_key = ()
                try:
                    self.overlay.clear()
                except Exception:
                    pass
                return
            followed = []
            for entry in entries:
                try:
                    if entry.get("focus"):
                        nt = resolve_focus(fresh)
                    else:
                        nt = resolve_named(entry.get("expression", ""), fresh)
                    if nt is None:
                        continue
                    if entry.get("label"):
                        nt.label = entry["label"]
                    if entry.get("reason"):
                        nt.reason = entry["reason"]
                    entry["target"] = nt
                    followed.append(entry)
                except Exception:
                    continue
            new_targets = [e["target"] for e in followed]
            with self._lock:
                old_sig = [(t.name, t.kind, tuple(t.rect))
                           for t in self._shown]
                new_sig = [(t.name, t.kind, tuple(t.rect))
                           for t in new_targets]
                self._entries = followed
                self._shown = list(new_targets)
                try:
                    self._shown_key = tuple(
                        new_targets[0].state_key) if new_targets else ()
                except Exception:
                    self._shown_key = ()
            if new_sig != old_sig:
                try:
                    if new_targets:
                        self.overlay.show(list(new_targets))
                    else:
                        self.overlay.clear()
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"workspace invalidation failed: {e}")

    def present(self, expression: str = "", focus: bool = False,
                label: str = "", reason: str = "") -> Optional[SpatialTarget]:
        """Resolve from the CURRENT bus state and show. None when the
        target cannot be located reliably (caller answers verbally)."""
        try:
            if self.bus is None:
                self.last_error = "no visual state bus"
                return None
            state = None
            try:
                state = self.bus.get_fresh()
            except Exception:
                try:
                    state = self.bus.get()
                except Exception:
                    state = None
            if state is None:
                self.last_error = "no fresh visual state"
                return None
            target = (resolve_focus(state) if focus or not (expression or "")
                      else resolve_named(expression, state))
            if target is None:
                self.last_error = (
                    f"no reliable geometry for {expression or 'focus'!r}")
                return None
            if label:
                target.label = label[:40]
            if reason:
                target.reason = reason[:120]
            return self.show_target(target, expression=expression or "",
                                    focus=bool(focus or not (expression or "")))
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning(f"workspace present failed: {e}")
            return None

    def show_target(self, target: SpatialTarget, expression: str = "",
                    focus: bool = False) -> Optional[SpatialTarget]:
        try:
            if target is None or not _rect_ok(target.rect):
                return None
            if not target.is_live(self.bus, self.ttl_seconds):
                self.last_error = "target stale before display"
                return None
            if not self.overlay.show([target]):
                self.last_error = getattr(
                    self.overlay, "last_error", "overlay refused") or \
                    "overlay refused"
                return None
            with self._lock:
                self._shown = [target]
                self._entries = [{"target": target,
                                  "expression": expression or "",
                                  "focus": focus,
                                  "label": target.label,
                                  "reason": target.reason}]
                try:
                    self._shown_key = tuple(target.state_key)
                except Exception:
                    self._shown_key = ()
            return target
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning(f"workspace show failed: {e}")
            return None

    def show_many(self, targets) -> list:
        """Show several relevant regions; returns the ones accepted."""
        shown = []
        try:
            live = [t for t in (targets or [])
                    if t is not None and _rect_ok(t.rect)
                    and t.is_live(self.bus, self.ttl_seconds)]
            if not live:
                return []
            if not self.overlay.show(live):
                self.last_error = "overlay refused multi-show"
                return []
            with self._lock:
                self._shown = list(live)
                self._entries = [
                    {"target": t, "expression": "", "focus": False,
                     "label": t.label, "reason": t.reason} for t in live]
                try:
                    self._shown_key = tuple(live[0].state_key)
                except Exception:
                    self._shown_key = ()
            shown = live
        except Exception as e:
            self.last_error = str(e)[:200]
        return shown

    def clear(self) -> None:
        try:
            with self._lock:
                self._shown = []
                self._shown_key = ()
                self._entries = []
            self.overlay.clear()
        except Exception as e:
            self.last_error = str(e)[:200]

    def current(self) -> list:
        """Shown targets still backed by the live bus state."""
        try:
            with self._lock:
                live = [t for t in self._shown
                        if t.is_live(self.bus, self.ttl_seconds)]
                if len(live) != len(self._shown):
                    self._shown = live
                    live_ids = {id(t) for t in live}
                    self._entries = [e for e in self._entries
                                     if id(e.get("target")) in live_ids]
                    if not live:
                        try:
                            self.overlay.clear()
                        except Exception:
                            pass
                return list(live)
        except Exception:
            return []

    def status(self) -> dict:
        """Capability self-model: AVAILABLE / DEGRADED / UNAVAILABLE."""
        try:
            overlay_ready = bool(getattr(self.overlay, "ready",
                                         getattr(self.overlay, "is_up",
                                                 False)))
            if self.bus is None:
                return {"state": "UNAVAILABLE",
                        "reason": "no visual state bus"}
            fresh = None
            try:
                fresh = self.bus.get_fresh()
            except Exception:
                pass
            if not overlay_ready:
                return {"state": "DEGRADED",
                        "reason": getattr(
                            self.overlay, "last_error",
                            "overlay down") or "overlay down"}
            if fresh is None:
                return {"state": "DEGRADED",
                        "reason": "no fresh visual state"}
            return {"state": "AVAILABLE", "reason": "overlay up, state fresh"}
        except Exception as e:
            return {"state": "UNAVAILABLE", "reason": str(e)[:120]}

    def shutdown(self) -> None:
        try:
            if self._unsub is not None:
                try:
                    self._unsub()
                except Exception:
                    pass
                self._unsub = None
        except Exception:
            pass
        try:
            self.overlay.shutdown()
        except Exception:
            pass


def get_workspace(bus=None, overlay=None) -> SpatialWorkspace:
    return SpatialWorkspace(bus=bus, overlay=overlay)
