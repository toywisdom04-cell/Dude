"""Phase 14: Visual State Bus.

One authoritative, continuously updated representation of DUDE's current
visual/environment state. Existing perception producers publish their
(s Already-observed) snapshots; reasoning, answers, and future UI
components all read the same current state.

Design rules (mission constraints):
- STATE MODEL, not an archive: exactly one current state, replaced on
  every publish. No history list, no screenshot database, no pixel
  bytes stored anywhere on the bus (only presence/age metadata).
- Freshness/provenance on every state: captured_at, source, capture
  method, and goal travel with the state so stale observations are
  distinguishable from live ones.
- Complements CapabilityBus (which carries live component refs); the
  bus carries the latest visual STATE. Never replaces perception
  logic: producers pass their own snapshots in, the bus only copies
  bounded summaries out of them.
- No cloud, no model, no app-specific content: field set is the
  generic OS-level vocabulary (app, window, controls, OCR excerpt).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_MAX_CONTROLS = 12
_MAX_OCR_CHARS = 300
_MAX_TEXT_CHARS = 120


def _clip(text: Any, limit: int) -> str:
    try:
        s = str(text or "")
    except Exception:
        return ""
    return s[:limit]


def describe_snapshot(snapshot: Any, goal: str = "") -> str:
    """Canonical text rendering of one perception snapshot.

    Single formatter shared by the bus and any consumer that needs the
    same words for reasoning and answers (no per-consumer variants).
    Bounded; never includes pixels.
    """
    try:
        app = _clip(getattr(snapshot, "active_app", "unknown"), 80)
        win = getattr(snapshot, "active_window", "") or {}
        title = _clip(win.get("title", "") if isinstance(win, dict)
                      else getattr(win, "title", ""), _MAX_TEXT_CHARS)
        focused = getattr(snapshot, "focused_control", None)
        fname = _clip(getattr(focused, "name", ""), 100) if focused else ""
        ctrls = getattr(snapshot, "controls", "") or []
        names = []
        for c in ctrls[:_MAX_CONTROLS]:
            n = _clip(getattr(c, "name", ""), 60)
            if n:
                names.append(n)
        ocr = _clip(getattr(snapshot, "ocr_text", ""), _MAX_OCR_CHARS)
        changed = bool(getattr(snapshot, "change_detected", False))
        lines = [
            f"app: {app}",
            f"window: {title or '(no title)'}",
            f"focused: {fname or '(none)'}",
            f"controls({len(ctrls)}): "
            f"{', '.join(names) if names else '(none)'}",
            f"ocr: {ocr or '(none)'}",
            f"changed: {'yes' if changed else 'no'}",
        ]
        if goal:
            lines.append(f"goal: {_clip(goal, _MAX_TEXT_CHARS)}")
        return "\n".join(lines)
    except Exception as e:
        log.warning(f"describe_snapshot failed: {e}")
        return ""


@dataclass
class VisualState:
    """One canonical visual state (a bounded summary, never pixels)."""
    active_app: str = "unknown"
    window_title: str = ""
    window_bounds: tuple = ()
    focused_name: str = ""
    focused_rect: tuple = ()
    control_names: tuple = ()
    control_count: int = 0
    # Phase 15 spatial geometry (bounded, aligned with control_names;
    # rects are (x, y, w, h) screen coordinates from UIA evidence).
    control_rects: tuple = ()
    control_roles: tuple = ()
    control_enabled: tuple = ()
    # Phase 15 OCR geometry (bounded (text, rect) items for OCR fallback).
    ocr_items: tuple = ()
    ocr_excerpt: str = ""
    change_detected: bool = False
    screenshot_present: bool = False
    screenshot_age: float = 0.0
    screen_summary: str = ""
    # Provenance / freshness
    captured_at: float = field(default_factory=time.time)
    published_at: float = field(default_factory=time.time)
    source: str = ""
    capture_method: str = ""
    goal: str = ""

    def age_seconds(self, now: Optional[float] = None) -> float:
        try:
            return max(0.0, (time.time() if now is None else now)
                       - self.captured_at)
        except Exception:
            return 0.0

    def is_stale(self, ttl_seconds: float) -> bool:
        return self.age_seconds() > ttl_seconds

    def key(self) -> tuple:
        return (self.active_app, self.window_title)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["age_seconds"] = self.age_seconds()
        return d


def _rect_tuple(r: Any) -> tuple:
    try:
        if r is None:
            return ()
        if isinstance(r, (tuple, list)) and len(r) >= 4:
            return tuple(int(v) for v in r[:4])
        return (int(getattr(r, "x", 0)), int(getattr(r, "y", 0)),
                int(getattr(r, "w", 0)), int(getattr(r, "h", 0)))
    except Exception:
        return ()


class VisualStateBus:
    """Thread-safe single-slot bus for the current visual state."""

    def __init__(self, stale_after_seconds: float = 5.0):
        self.stale_after_seconds = float(stale_after_seconds or 5.0)
        self._lock = threading.RLock()
        self._current: Optional[VisualState] = None
        self._last_snapshot: Any = None
        self._last_transition: dict = {}
        self._subscribers: list[Callable[[VisualState], None]] = []
        self.publish_count = 0

    def publish(self, snapshot: Any, source: str = "",
                goal: str = "") -> Optional[VisualState]:
        """Replace the current state from a producer's snapshot.

        Copies bounded summaries only. Screenshot bytes are NEVER
        stored (presence + age metadata only). Returns the new state,
        or None when the snapshot was unusable.
        """
        if snapshot is None:
            return None
        try:
            win = getattr(snapshot, "active_window", "") or {}
            title = (win.get("title", "") if isinstance(win, dict)
                     else getattr(win, "title", ""))
            if not title:
                title = getattr(snapshot, "window_title", "")
            focused = getattr(snapshot, "focused_control", None)
            ctrls = list(getattr(snapshot, "controls", "") or [])
            names = []
            rects = []
            roles = []
            enabled = []
            for c in ctrls[:_MAX_CONTROLS]:
                n = _clip(getattr(c, "name", ""), 60)
                if n:
                    names.append(n)
                    try:
                        rects.append((
                            int(getattr(c, "x", 0)),
                            int(getattr(c, "y", 0)),
                            int(getattr(c, "w", 0)),
                            int(getattr(c, "h", 0))))
                    except Exception:
                        rects.append(())
                    roles.append(_clip(
                        getattr(c, "role", "")
                        or getattr(c, "ctype", ""), 40))
                    try:
                        enabled.append(bool(getattr(c, "enabled", True)))
                    except Exception:
                        enabled.append(True)
            ocr_regs = list(getattr(snapshot, "ocr_regions", "") or [])
            ocr_items = []
            for r in ocr_regs[:20]:
                t = _clip(getattr(r, "text", ""), 80)
                if not t:
                    continue
                try:
                    rect = (int(getattr(r, "x", 0)), int(getattr(r, "y", 0)),
                            int(getattr(r, "w", 0)), int(getattr(r, "h", 0)))
                except Exception:
                    rect = ()
                ocr_items.append((t, rect))
            try:
                method = getattr(snapshot, "capture_method", "")
                method_name = getattr(method, "name", "") or str(method or "")
            except Exception:
                method_name = ""
            state = VisualState(
                active_app=_clip(
                    getattr(snapshot, "active_app", "unknown"), 80),
                window_title=_clip(title, _MAX_TEXT_CHARS),
                window_bounds=_rect_tuple(
                    getattr(snapshot, "window_bounds", None)),
                focused_name=_clip(
                    getattr(focused, "name", ""), 100) if focused else "",
                focused_rect=_rect_tuple(focused) if focused else (),
                control_names=tuple(names),
                control_count=len(ctrls),
                control_rects=tuple(rects),
                control_roles=tuple(roles),
                control_enabled=tuple(enabled),
                ocr_items=tuple(ocr_items),
                ocr_excerpt=_clip(
                    getattr(snapshot, "ocr_text", ""), _MAX_OCR_CHARS),
                change_detected=bool(
                    getattr(snapshot, "change_detected", False)),
                screenshot_present=bool(
                    getattr(snapshot, "screenshot", None) is not None),
                screenshot_age=float(
                    getattr(snapshot, "screenshot_age", 0.0) or 0.0),
                captured_at=time.time(),
                published_at=time.time(),
                source=_clip(source, 80),
                capture_method=_clip(method_name, 40),
                goal=_clip(goal, _MAX_TEXT_CHARS),
            )
            with self._lock:
                prev = self._current
                self._current = state
                self._last_snapshot = snapshot
                self.publish_count += 1
                if prev is None:
                    self._last_transition = {
                        "from": None, "to": state.key(),
                        "at": state.published_at,
                    }
                elif prev.key() != state.key():
                    self._last_transition = {
                        "from": prev.key(), "to": state.key(),
                        "at": state.published_at,
                    }
                subs = list(self._subscribers)
            for fn in subs:
                try:
                    fn(state)
                except Exception as e:
                    log.warning(f"visual-state subscriber failed: {e}")
            return state
        except Exception as e:
            log.warning(f"visual-state publish failed: {e}")
            return None

    def get(self) -> Optional[VisualState]:
        """Latest state (stale or fresh — check is_stale)."""
        with self._lock:
            return self._current

    def get_fresh(self) -> Optional[VisualState]:
        """Latest state, or None when missing/stale."""
        with self._lock:
            cur = self._current
        if cur is None:
            return None
        if cur.is_stale(self.stale_after_seconds):
            return None
        return cur

    def describe(self) -> str:
        """Canonical words for the current state (reasoning == answers)."""
        with self._lock:
            cur = self._current
            snap = self._last_snapshot
        if cur is None:
            return ""
        try:
            if snap is not None:
                text = describe_snapshot(snap, cur.goal)
            else:
                text = (f"app: {cur.active_app}\n"
                        f"window: {cur.window_title or '(no title)'}\n"
                        f"focused: {cur.focused_name or '(none)'}\n"
                        f"controls({cur.control_count}): "
                        f"{', '.join(cur.control_names) or '(none)'}\n"
                        f"ocr: {cur.ocr_excerpt or '(none)'}\n"
                        f"changed: {'yes' if cur.change_detected else 'no'}")
            if cur.is_stale(self.stale_after_seconds):
                text += (f"\n(stale: {cur.age_seconds():.1f}s old)")
            return text
        except Exception as e:
            log.warning(f"visual-state describe failed: {e}")
            return ""

    def subscribe(self, fn: Callable[[VisualState], None]) -> Callable:
        """Register a consumer; returns an unsubscribe callable."""
        with self._lock:
            self._subscribers.append(fn)

        def _unsub():
            try:
                with self._lock:
                    if fn in self._subscribers:
                        self._subscribers.remove(fn)
            except Exception:
                pass

        return _unsub

    def last_transition(self) -> dict:
        """Previous -> current app/window key (bounded, for live proof)."""
        with self._lock:
            return dict(self._last_transition)

    def stats(self) -> dict:
        with self._lock:
            return {
                "publish_count": self.publish_count,
                "has_state": self._current is not None,
                "subscribers": len(self._subscribers),
                "stale_after_seconds": self.stale_after_seconds,
            }


_bus: Optional[VisualStateBus] = None
_bus_lock = threading.Lock()


def get_visual_state_bus(
        stale_after_seconds: float = 5.0) -> VisualStateBus:
    """Process singleton (tests construct their own instances)."""
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = VisualStateBus(
                stale_after_seconds=stale_after_seconds)
        return _bus
