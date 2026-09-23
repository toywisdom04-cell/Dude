"""Continuous perception service (Phase 9, Part A).

A thin always-on layer over the existing PerceptionEngine — not a
replacement. One shared cache (the engine's), three lanes:

FAST (cheap, frequent): foreground HWND/app/title, focused control,
    UIA-tree signature, screen hash. No OCR, no screenshots, no model.
MEDIUM (on meaningful change only): UIA subtree refresh, dialog
    classification, targeted OCR of one region.
SLOW (explicit demand only): full screenshot / full OCR / CV, through
    the existing engine.observe() path.

Event-driven first: native WinEvents (foreground, focus, create,
destroy, menu, dialog) mark state dirty; a cheap poll loop covers what
events miss. When events are unavailable the service degrades to
polling-only and says so.

Screenshots are EPHEMERAL: a small TTL ring buffer for active
perception/recovery only. They are never written to Memory, never
stored in procedures, never attached to learning traces. Only
structured SemanticObservations persist, sanitized, deduplicated.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional

from .state import PerceptionLevel

log = logging.getLogger(__name__)


class ChangeClass(Enum):
    """How important a screen change is. Drives lane escalation."""
    IRRELEVANT = "irrelevant"            # cursor, clock, repaint noise
    LOW = "low"                          # focus moved, title tweak
    TASK_RELEVANT = "task_relevant"      # involves the active task's app
    ACTION_RELEVANT = "action_relevant"  # window/popup/menu/dialog appeared
    RECOVERY_RELEVANT = "recovery_relevant"  # modal/error demanding handling
    USER_RELEVANT = "user_relevant"      # user drove to another app


@dataclass
class SemanticObservation:
    """Structured, storable screen state. No pixels, ever."""
    captured_at: float = 0.0
    active_app: str = "unknown"
    window_title: str = ""
    hwnd: int = 0
    wclass: str = ""
    focused_name: str = ""
    focused_ctype: str = ""
    dialog_kind: str = "none"
    change: str = ChangeClass.IRRELEVANT.value
    summary: str = ""
    confidence: float = 0.0
    source: str = "poll"  # "event" or "poll"
    related: str = ""  # background births/deaths, e.g. "appeared notepad.exe#123 't'"
    delta: Dict[str, str] = field(default_factory=dict)  # scene delta vs previous obs
    csig: str = ""  # control-signature hash: part of identity so a
    # control-tree change is never deduped away as "same observation"
    rsig: tuple = ()  # reason kinds that fired this tick (window_state,
    # menu_opened, ...). Event ticks must emit even when every visible
    # field is unchanged — otherwise their deltas die in dedup.
    controls_summary: str = ""  # Summary of controls for pattern detection
    control_types: list[str] = field(default_factory=list)  # List of control types present

    def key(self) -> tuple:
        return (self.active_app, self.window_title, self.hwnd,
                self.focused_name, self.dialog_kind, self.change,
                self.related, self.csig, self.rsig)

    def delta_summary(self) -> str:
        if not self.delta:
            return "no-change"
        return "; ".join(f"{k}={v}" for k, v in self.delta.items())

    def to_fact(self) -> str:
        base = (f"screen: app={self.active_app} window={self.window_title!r} "
                f"focus={self.focused_name!r} dialog={self.dialog_kind} "
                f"change={self.change} note={self.summary[:160]}")
        if self.related:
            base += f" related={self.related}"
        if self.delta:
            base += f" delta={self.delta_summary()[:160]}"
        return base


@dataclass
class _Frame:
    captured_at: float
    image_bytes: bytes
    purpose: str


class FrameRing:
    """Bounded TTL ring for ephemeral screenshots.

    Frames exist for active perception/recovery comparison only.
    Anything older than `ttl_seconds` is purged on every push and on
    explicit sweep. The ring never leaves this process.
    """

    def __init__(self, max_frames: int = 4, ttl_seconds: float = 60.0):
        self._frames: Deque[_Frame] = deque(maxlen=max_frames)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self.expired_deleted = 0

    def push(self, image_bytes: bytes, purpose: str) -> int:
        now = time.time()
        with self._lock:
            self._frames.append(_Frame(now, image_bytes, purpose))
            return self._purge_locked(now)

    def sweep(self) -> int:
        with self._lock:
            return self._purge_locked(time.time())

    def _purge_locked(self, now: float) -> int:
        before = len(self._frames)
        self._frames = deque(
            (f for f in self._frames if now - f.captured_at <= self._ttl),
            maxlen=self._frames.maxlen,
        )
        expired = before - len(self._frames)
        self.expired_deleted += expired
        return expired

    def live_count(self) -> int:
        self.sweep()
        with self._lock:
            return len(self._frames)


class LatencyMetrics:
    """Last-N latency samples per named operation."""

    def __init__(self, keep: int = 120):
        self._samples: Dict[str, Deque[float]] = {}
        self._keep = keep
        self._lock = threading.Lock()

    def record(self, name: str, ms: float) -> None:
        with self._lock:
            dq = self._samples.setdefault(name, deque(maxlen=self._keep))
            dq.append(float(ms))

    def summary(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        with self._lock:
            for name, dq in self._samples.items():
                if not dq:
                    continue
                s = sorted(dq)
                out[name] = {
                    "count": float(len(s)),
                    "p50": s[len(s) // 2],
                    "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
                    "avg": sum(s) / len(s),
                }
        return out


def _control_identity(c) -> str:
    """Stable identity for delta tracking: AutomationId preferred, else
    (type, name). Rect/object identity deliberately EXCLUDED so
    coordinate jitter and UIA wrapper churn never become events."""
    try:
        aid = (getattr(c, "automation_id", "") or "").strip()
    except Exception:
        aid = ""
    if aid:
        return "aid:" + aid
    try:
        return "tn:%s|%s" % (getattr(c, "ctype", "") or "",
                             getattr(c, "name", "") or "")
    except Exception:
        return "tn:|"


def _control_sig_entry(c, value: str = "") -> tuple:
    try:
        enabled = bool(getattr(c, "enabled", True))
    except Exception:
        enabled = True
    try:
        name = getattr(c, "name", "") or ""
    except Exception:
        name = ""
    return (enabled, name, value)


def compute_control_delta(prev: Dict[str, tuple],
                          cur: Dict[str, tuple],
                          max_names: int = 3) -> Dict[str, str]:
    """Control-level BEFORE->AFTER delta with jitter filtering.

    Inputs map stable identity -> (enabled, name, sampled_value).
    Rects never participate (jitter-proof); timestamps/wrapper identity
    never participate (churn-proof). Empty dict = no meaningful change.
    """
    d: Dict[str, str] = {}
    if not prev:
        return d
    prev_keys, cur_keys = set(prev), set(cur)
    added = sorted(cur_keys - prev_keys)
    removed = sorted(prev_keys - cur_keys)
    if added:
        d["control_appeared"] = "%d:%s" % (
            len(added), ",".join(a.split(":", 1)[-1][:30]
                                 for a in added[:max_names]))
    if removed:
        d["control_removed"] = "%d:%s" % (
            len(removed), ",".join(r.split(":", 1)[-1][:30]
                                   for r in removed[:max_names]))
    for k in sorted(prev_keys & cur_keys):
        try:
            pe, pn, pv = prev[k]
            ce, cn, cv = cur[k]
        except Exception:
            continue
        if pe != ce:
            d.setdefault("control_enabled_changed",
                         "%s:%s->%s" % (k.split(":", 1)[-1][:30],
                                        pe, ce))
        if cn != pn:
            d.setdefault("control_text_changed",
                         "%s:%r->%r" % (k.split(":", 1)[-1][:30],
                                        pn[:30], cn[:30]))
        elif cv != pv and (cv or pv):
            d.setdefault("control_value_changed",
                         "%s" % k.split(":", 1)[-1][:40])
    return d


def _sig_hash(sig: Dict[str, tuple]) -> str:
    """Stable string over the control signature for observation keys."""
    try:
        parts = []
        for k in sorted(sig):
            try:
                e, n, v = sig[k]
            except Exception:
                continue
            parts.append("%s=%s/%s/%s" % (k, e, n[:40], v[:40]))
        return ";".join(parts)[:2000]
    except Exception:
        return ""


def compute_scene_delta(prev: Optional[SemanticObservation],
                          cur: SemanticObservation) -> Dict[str, str]:
    """Structured BEFORE->AFTER delta. First observation yields {'initial'}.
    Only meaningful transitions are reported; repaint noise is nothing."""
    if prev is None:
        return {"initial": "first observation"}
    d: Dict[str, str] = {}
    if cur.active_app != prev.active_app or cur.hwnd != prev.hwnd:
        d["fg_changed"] = f"{prev.active_app}#{prev.hwnd}->" \
                          f"{cur.active_app}#{cur.hwnd}"
    if cur.dialog_kind != prev.dialog_kind:
        if prev.dialog_kind in ("none", ""):
            d["dialog_added"] = cur.dialog_kind
        elif cur.dialog_kind in ("none", ""):
            d["dialog_removed"] = prev.dialog_kind
        else:
            d["dialog_changed"] = f"{prev.dialog_kind}->{cur.dialog_kind}"
    if cur.focused_name != prev.focused_name and cur.focused_name:
        d["focus_changed"] = f"{prev.focused_name[:40]!r}->" \
                             f"{cur.focused_name[:40]!r}"
        # Focus moving among selectable items IS a selection change.
        try:
            _sel_types = ("tabitem", "listitem", "menuitem", "treeitem")
            _ct = (cur.focused_ctype or "").lower()
            if any(t in _ct for t in _sel_types):
                d["selection_changed"] = \
                    f"{prev.focused_name[:40]!r}->{cur.focused_name[:40]!r}"
        except Exception:
            pass
    for r in (cur.related or "").split("; "):
        if r.startswith("window_appeared:"):
            d.setdefault("popup_added", r.split(":", 1)[1][:60])
        elif r.startswith("window_closed:"):
            d.setdefault("popup_removed", r.split(":", 1)[1][:60])
    return d


class ObservationClass(Enum):
    """Pipeline verdict for one observation (§6). Only TASK_RELEVANT and
    REUSABLE_CANDIDATE may enter persistent memory; SECRET_SENSITIVE
    never persists anywhere; the rest stay transient working state."""
    TRANSIENT = "transient"
    TASK_RELEVANT = "task_relevant"
    REUSABLE_CANDIDATE = "reusable_candidate"
    SECRET_SENSITIVE = "secret_sensitive"
    IRRELEVANT = "irrelevant"


@dataclass
class LiveSceneState:
    """Compact semantic live truth (§3). No pixels, ever. ONE shared
    instance flows to task + voice layers via get_scene()."""
    scene_version: int = 0
    captured_at: float = 0.0
    active_app: str = "unknown"
    window_title: str = ""
    hwnd: int = 0
    wclass: str = ""
    focused_name: str = ""
    focused_ctype: str = ""
    visible_count: int = 0
    dialog_kind: str = "none"
    popup_summary: str = ""
    task_app: str = ""
    task_hwnd: int = 0
    recent_events: List[str] = field(default_factory=list)
    delta_summary: str = "no-change"
    confidence: float = 0.0
    controls_summary: str = ""  # e.g. "11 controls: 2 Edit, 3 Button"

    def freshness_ms(self) -> float:
        return (time.time() - self.captured_at) * 1000.0 if \
            self.captured_at else float("inf")

    def is_stale(self, threshold_ms: float = 10000.0) -> bool:
        return self.freshness_ms() > threshold_ms


class ObservationLog:
    """Bounded TTL working memory of structured observations.

    Facts ABOUT the screen, never pixels. Identical consecutive keys are
    deduplicated (no repeat writes); entries older than ttl_seconds are
    purged on push/sweep. Promotion to long-term Memory stays explicit
    via remember_observation().
    """

    def __init__(self, max_entries: int = 64, ttl_seconds: float = 300.0):
        self._entries: Deque[tuple] = deque(maxlen=max_entries)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self.deduped = 0
        self.expired = 0

    def push(self, obs: SemanticObservation) -> bool:
        """Store unless identical to the newest entry. Returns stored?"""
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            if self._entries and self._entries[-1][1].key() == obs.key():
                self.deduped += 1
                return False
            self._entries.append((now, obs))
            return True

    def sweep(self) -> int:
        with self._lock:
            return self._purge_locked(time.time())

    def _purge_locked(self, now: float) -> int:
        before = len(self._entries)
        self._entries = deque(
            (e for e in self._entries if now - e[0] <= self._ttl),
            maxlen=self._entries.maxlen)
        n = before - len(self._entries)
        self.expired += n
        return n

    def recent(self, n: int = 10) -> List[SemanticObservation]:
        self.sweep()
        with self._lock:
            return [e[1] for e in list(self._entries)[-n:]]

    def __len__(self) -> int:
        self.sweep()
        with self._lock:
            return len(self._entries)


# WinEvent ids we subscribe to (native, cheap, no polling).
_WINEVENT_FOREGROUND = 0x0003
_WINEVENT_MENUSTART = 0x0004
_WINEVENT_MENUEND = 0x0005
_WINEVENT_DIALOGSTART = 0x0010
_WINEVENT_OBJECT_FOCUS = 0x8005
_WINEVENT_OBJECT_CREATE = 0x8000
_WINEVENT_OBJECT_DESTROY = 0x8001
_WINEVENT_OBJECT_VALUECHANGE = 0x800C
_WINEVENT_OBJECT_STATECHANGE = 0x800A
_WINEVENT_OBJECT_SELECTION = 0x8006
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002


class PerceptionService:
    """Always-on perception around one shared PerceptionEngine."""

    def __init__(
        self,
        perception_engine,
        capture_fn: Optional[Callable] = None,
        memory=None,
        fast_hz: float = 10.0,
        idle_hz: float = 2.0,
        frame_ttl_seconds: float = 60.0,
        max_frames: int = 4,
    ):
        self.engine = perception_engine
        self._capture_fn = capture_fn
        self._memory = memory
        self._base_fast_interval = 1.0 / max(fast_hz, 0.5)
        self._base_idle_interval = 1.0 / max(idle_hz, 0.25)
        self._fast_interval = self._base_fast_interval
        self._idle_interval = self._base_idle_interval
        self._frames = FrameRing(max_frames=max_frames,
                                 ttl_seconds=frame_ttl_seconds)
        self.metrics = LatencyMetrics()
        self._counters = {
            "frames_seen": 0,
            "frames_discarded": 0,
            "observations_created": 0,
            "observations_deduped": 0,
            "persistent_memories_created": 0,
            "expired_items_deleted": 0,
            # Fast ticks where nothing changed and no refresh ran: proof
            # redundant work is skipped, not just unmeasured.
            "redundant_ticks_avoided": 0,
        }
        # Structured perceptual working memory (TTL + dedup). Promotion to
        # long-term Memory stays explicit via remember_observation().
        self.observations = ObservationLog()
        self._stop = threading.Event()
        self._fast_thread: Optional[threading.Thread] = None
        self._hook_thread: Optional[threading.Thread] = None
        self._hook_handle = None
        self._hook_callbacks = []
        self.events_enabled = False
        self._lock = threading.Lock()
        self._last_fast: Dict[str, Any] = {}
        self._last_observation: Optional[SemanticObservation] = None
        self._last_tree_sig: Any = None
        self._last_hash = None
        self._dirty = True
        self._dirty_reasons: List[tuple] = []  # (timestamp, reason)
        self._idle_ticks = 0
        self._task_context: Dict[str, Any] = {}
        # Adaptive activity tiers (§2): STABLE sleeps, BURST samples fast
        # for a few seconds after meaningful change, then decays.
        self._activity = "NORMAL"
        self._burst_until = 0.0
        self._scene_version = 0
        self._dialog_histogram: Dict[str, int] = {}
        # Background window-set tracking: HWND -> (exe, title). Foreground
        # identity alone misses windows born/dying behind the active app.
        self._last_window_set: Dict[int, tuple] = {}
        self._last_window_check = 0.0
        self._window_check_interval = 2.0
        self._recent_window_events: Deque[Dict[str, Any]] = deque(maxlen=32)
        # Control-level delta state (§4): stable-identity signatures of
        # the last medium-lane snapshot + fg window-state. Rects never
        # stored here (jitter-proof by construction).
        self._last_control_sig: Dict[str, tuple] = {}
        self._last_control_summary: str = ""
        self._last_wstate: str = ""
        # Medium-lane backstop: silent in-place changes (menu closes
        # without MENUEND, value edits without VALUECHANGE) are invisible
        # to the fast lane. A bounded periodic refresh caps detection
        # latency for everything events miss. Cheap (~20ms/5s).
        self._last_medium: float = 0.0
        self._medium_backstop_interval: float = 5.0
        try:
            from .procedure_learner import SecretSanitizer
            self._sanitizer = SecretSanitizer()
        except Exception:
            self._sanitizer = None

    # ---------------- lifecycle ----------------

    def start(self) -> None:
        """Start event hook + fast loop. Idempotent."""
        if self._fast_thread and self._fast_thread.is_alive():
            return
        self._stop.clear()
        self._start_hook_thread()
        self._fast_thread = threading.Thread(
            target=self._fast_loop, name="dude-perception-fast",
            daemon=True)
        self._fast_thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._hook_handle:
                import ctypes
                ctypes.windll.user32.UnhookWinEvent(self._hook_handle)
        except Exception:
            pass
        self._hook_handle = None
        for t in (self._fast_thread, self._hook_thread):
            try:
                if t and t.is_alive():
                    t.join(timeout=2.0)
            except Exception:
                pass
        self._fast_thread = None
        self._hook_thread = None

    def set_task_context(self, app: str = "", hwnd: int = 0) -> None:
        with self._lock:
            self._task_context = {"app": (app or "").lower(),
                                  "hwnd": hwnd or 0}

    # ---------------- adaptive activity tiers ----------------

    # Tier -> (fast_hz, idle_hz). BURST is transient (see burst()).
    _TIERS = {
        "STABLE": (1.0, 0.5),
        "NORMAL": (10.0, 2.0),
        "ACTIVE": (15.0, 5.0),
        "BURST": (20.0, 10.0),
    }

    def set_activity(self, level: str) -> str:
        """Pin the observation tier. Task layer raises ACTIVE during
        autonomous work; STABLE when the user is idle. Returns level."""
        level = (level or "NORMAL").upper()
        if level not in self._TIERS:
            level = "NORMAL"
        fast_hz, idle_hz = self._TIERS[level]
        with self._lock:
            self._activity = level
            self._fast_interval = 1.0 / fast_hz
            self._idle_interval = 1.0 / idle_hz
            self._burst_interval = 1.0 / self._TIERS["BURST"][0]
        return level

    def burst(self, seconds: float = 3.0) -> None:
        """Short burst of frequent observations, then auto-decay."""
        with self._lock:
            if not hasattr(self, "_burst_interval"):
                self._burst_interval = 1.0 / self._TIERS["BURST"][0]
            self._burst_until = max(self._burst_until,
                                    time.time() + seconds)

    # ---------------- shared live scene ----------------

    def get_scene(self) -> LiveSceneState:
        """ONE shared semantic scene for task + voice layers."""
        t0 = time.perf_counter()
        with self._lock:
            o = self._last_observation
            task = dict(self._task_context)
            recent = [e.get("kind", "") + ":" + str(e.get("hwnd", ""))
                      for e in list(self._recent_window_events)[-6:]]
            version = self._scene_version
            ctrl_summary = self._last_control_summary
        if o is None:
            scene = LiveSceneState()
        else:
            scene = LiveSceneState(
                scene_version=version, captured_at=o.captured_at,
                active_app=o.active_app, window_title=o.window_title,
                hwnd=o.hwnd, wclass=o.wclass,
                focused_name=o.focused_name,
                focused_ctype=o.focused_ctype,
                dialog_kind=o.dialog_kind,
                popup_summary=o.related[:160],
                task_app=task.get("app", ""), task_hwnd=task.get("hwnd", 0),
                recent_events=recent, delta_summary=o.delta_summary(),
                confidence=o.confidence,
                controls_summary=ctrl_summary)
        self.metrics.record("scene_update_ms",
                            (time.perf_counter() - t0) * 1000.0)
        return scene

    def classify_observation(self, obs: SemanticObservation):
        """Pipeline verdict (§6). Returns (ObservationClass, reason)."""
        try:
            fact = obs.to_fact()
        except Exception:
            fact = ""
        if self._sanitizer is not None:
            try:
                if self._sanitizer.contains_secrets(fact):
                    return (ObservationClass.SECRET_SENSITIVE,
                            "secret pattern in observation text")
            except Exception:
                pass
        if obs.change == ChangeClass.IRRELEVANT.value and not obs.related \
                and obs.dialog_kind in ("none", ""):
            return (ObservationClass.IRRELEVANT, "no meaningful change")
        if obs.dialog_kind not in ("none", ""):
            with self._lock:
                seen = self._dialog_histogram.get(obs.dialog_kind, 0)
            if seen >= 1:
                return (ObservationClass.REUSABLE_CANDIDATE,
                        f"dialog {obs.dialog_kind} seen repeatedly")
            return (ObservationClass.TASK_RELEVANT,
                    f"dialog {obs.dialog_kind} present")
        if obs.change in (ChangeClass.TASK_RELEVANT.value,
                          ChangeClass.ACTION_RELEVANT.value,
                          ChangeClass.RECOVERY_RELEVANT.value):
            return (ObservationClass.TASK_RELEVANT,
                    f"change={obs.change}")
        return (ObservationClass.TRANSIENT, "low/ambient change")

    def promote(self, obs: SemanticObservation) -> bool:
        """Persist only what the pipeline allows. Returns persisted?"""
        t0 = time.perf_counter()
        try:
            cls, _reason = self.classify_observation(obs)
            if cls in (ObservationClass.TASK_RELEVANT,
                       ObservationClass.REUSABLE_CANDIDATE):
                ok = self.remember_observation(obs)
            else:
                ok = False
            self.metrics.record("memory_write_ms",
                                (time.perf_counter() - t0) * 1000.0)
            return ok
        except Exception:
            return False

    # ---------------- fast lane ----------------

    def _fast_state(self) -> Dict[str, Any]:
        """Cheapest possible foreground/focus identity (no tree, no shot)."""
        t0 = time.perf_counter()
        hwnd, wclass, title, exe = 0, "", "", "unknown"
        focused_name, focused_ctype = "", ""
        wstate = ""
        try:
            import win32gui
            import win32process
            import psutil
            hwnd = win32gui.GetForegroundWindow() or 0
            if hwnd:
                try:
                    title = win32gui.GetWindowText(hwnd) or ""
                except Exception:
                    title = ""
                try:
                    wclass = win32gui.GetClassName(hwnd) or ""
                except Exception:
                    wclass = ""
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    exe = (psutil.Process(pid).name() or "unknown")
                except Exception:
                    pass
                # Two cheap win32 calls: minimized/maximized/restored.
                try:
                    if win32gui.IsIconic(hwnd):
                        wstate = "minimized"
                    elif win32gui.IsZoomed(hwnd):
                        wstate = "maximized"
                    else:
                        wstate = "restored"
                except Exception:
                    pass
        except Exception:
            pass
        try:
            st = None
            eng = self.engine
            get_map = getattr(eng, "_screentree", None)
            smap = get_map() if callable(get_map) else None
            if smap is not None and hasattr(smap, "find_focused"):
                frow = smap.find_focused()
                if isinstance(frow, dict):
                    focused_name = frow.get("name", "") or ""
                    focused_ctype = frow.get("ctype", "") or ""
        except Exception:
            pass
        self.metrics.record("fast_state_ms",
                            (time.perf_counter() - t0) * 1000.0)
        return {"hwnd": hwnd, "wclass": wclass, "title": title,
                "exe": exe, "focused_name": focused_name,
                "focused_ctype": focused_ctype, "wstate": wstate}

    def _classify(self, prev: Dict[str, Any],
                  cur: Dict[str, Any]) -> ChangeClass:
        if not prev:
            return ChangeClass.LOW
        if cur.get("hwnd") != prev.get("hwnd"):
            return ChangeClass.ACTION_RELEVANT
        if cur.get("exe") != prev.get("exe"):
            return ChangeClass.USER_RELEVANT
        if (cur.get("title") != prev.get("title")
                or cur.get("focused_name") != prev.get("focused_name")):
            with self._lock:
                tapp = (self._task_context.get("app") or "")
                thwnd = self._task_context.get("hwnd") or 0
            if tapp and (tapp in (cur.get("exe") or "").lower()
                         or (cur.get("title") or "").lower().startswith(tapp)):
                return ChangeClass.TASK_RELEVANT
            if thwnd and cur.get("hwnd") == thwnd:
                return ChangeClass.TASK_RELEVANT
            return ChangeClass.LOW
        return ChangeClass.IRRELEVANT

    def recent_window_events(self) -> List[Dict[str, Any]]:
        """Background window births/deaths noticed by the service."""
        with self._lock:
            return list(self._recent_window_events)

    def _window_set(self) -> Dict[int, tuple]:
        """Lightweight visible-window census: HWND -> (exe, title).

        Titles only for every window (fast); exe resolution is deferred
        to appeared/disappeared deltas by the caller.
        """
        t0 = time.perf_counter()
        out: Dict[int, tuple] = {}
        try:
            import win32gui
            hwnds: List[int] = []

            def cb(h, acc):
                try:
                    if win32gui.IsWindowVisible(h):
                        acc.append(h)
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(cb, hwnds)
            for h in hwnds:
                try:
                    title = win32gui.GetWindowText(h) or ""
                except Exception:
                    title = ""
                if title:
                    out[h] = ("", title)
        except Exception:
            pass
        self.metrics.record("window_census_ms",
                            (time.perf_counter() - t0) * 1000.0)
        return out

    @staticmethod
    def _exe_of(hwnd: int) -> str:
        try:
            import win32process
            import psutil
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return (psutil.Process(pid).name() or "unknown")
        except Exception:
            return "unknown"

    def _diff_windows(self) -> tuple:
        """Compare census to last; record events; return (appeared, gone).

        Each item is (hwnd, exe, title). Runs on dirty ticks and at most
        every _window_check_interval seconds as an event-miss backstop.
        """
        cur = self._window_set()
        with self._lock:
            prev = self._last_window_set
            self._last_window_set = cur
        # Carry resolved exes forward: the census reads titles only, so a
        # re-census must not wipe an exe resolved at appear time.
        for h, (_, title) in list(cur.items()):
            if h in prev and prev[h][0]:
                cur[h] = (prev[h][0], title)
        appeared, gone = [], []
        for h, (_, title) in cur.items():
            if h not in prev:
                exe = self._exe_of(h)
                appeared.append((h, exe, title))
                cur[h] = (exe, title)  # remember exe so close events name it
        for h, (exe, title) in prev.items():
            if h not in cur:
                gone.append((h, exe or self._exe_of(h), title))
        with self._lock:
            for h, exe, title in appeared:
                self._recent_window_events.append(
                    {"kind": "appeared", "hwnd": h, "exe": exe,
                     "title": title, "at": time.time()})
            for h, exe, title in gone:
                self._recent_window_events.append(
                    {"kind": "closed", "hwnd": h, "exe": exe,
                     "title": title, "at": time.time()})
        return appeared, gone

    def _fast_loop(self) -> None:
        interval = self._idle_interval
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                tick0 = time.perf_counter()
                cur = self._fast_state()
                now = time.time()
                with self._lock:
                    prev = dict(self._last_fast)
                    dirty_reasons = list(self._dirty_reasons)
                    self._dirty_reasons.clear()
                    had_dirty = self._dirty
                    self._dirty = False
                    window_due = (now - self._last_window_check
                                  >= self._window_check_interval)
                    if window_due:
                        self._last_window_check = now
                    backstop_due = (now - self._last_medium
                                    >= self._medium_backstop_interval)
                    if backstop_due:
                        self._last_medium = now
                    bursting = now < self._burst_until
                if dirty_reasons and isinstance(dirty_reasons[0], tuple):
                    ages = [now - ts for ts, _ in dirty_reasons]
                    self.metrics.record("event_to_state_ms",
                                        max(ages) * 1000.0)
                    dirty_reasons = [r for _, r in dirty_reasons]
                if window_due or had_dirty:
                    appeared, gone = self._diff_windows()
                    for h, exe, title in appeared:
                        dirty_reasons.append(
                            f"window_appeared:{exe}#{h}:{title[:40]}")
                    for h, exe, title in gone:
                        dirty_reasons.append(
                            f"window_closed:{exe}#{h}:{title[:40]}")
                    if appeared or gone:
                        had_dirty = True
                if backstop_due:
                    dirty_reasons.append("backstop")
                    had_dirty = True
                    with self._lock:
                        self._counters["backstop_refreshes"] = \
                            self._counters.get("backstop_refreshes", 0) + 1
                # Foreground window-state transitions (min/max/restore).
                with self._lock:
                    _prev_ws = self._last_wstate
                    if cur.get("wstate"):
                        self._last_wstate = cur["wstate"]
                if _prev_ws and cur.get("wstate") \
                        and cur["wstate"] != _prev_ws:
                    dirty_reasons.append(
                        f"window_state:{_prev_ws}->{cur['wstate']}")
                    had_dirty = True
                change = self._classify(prev, cur)
                self.metrics.record("change_detect_ms",
                                    (time.perf_counter() - tick0) * 1000.0)
                if change is not ChangeClass.IRRELEVANT or had_dirty:
                    self._on_change(cur, change, dirty_reasons)
                    interval = self._burst_interval if bursting \
                        else self._fast_interval
                    with self._lock:
                        self._idle_ticks = 0
                else:
                    with self._lock:
                        self._idle_ticks += 1
                        self._counters["redundant_ticks_avoided"] += 1
                        if self._idle_ticks > 20:
                            interval = self._idle_interval
                        if bursting:
                            interval = min(interval, self._burst_interval)
                with self._lock:
                    self._last_fast = cur
            except Exception as e:
                log.warning(f"Perception fast loop fault: {e}")
            self.metrics.record("fast_tick_ms",
                                (time.perf_counter() - t0) * 1000.0)
            self._stop.wait(interval)

    def _sample_edit_values(self, hwnd: int, ctrls) -> Dict[str, str]:
        """ValuePattern read of up to 8 edit-type controls + toggle state
        of check/radio controls, keyed by the same stable identity as
        the signature. Capped + timed: edit text is the only sampled
        value (labels carry text in their names)."""
        t0 = time.perf_counter()
        out: Dict[str, str] = {}
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                try:
                    root = auto.ControlFromHandle(hwnd) if hwnd else None
                except Exception:
                    root = None
                if root is None:
                    return out
                found = []

                def visit(node, depth):
                    if depth > 6 or len(found) >= 8:
                        return
                    try:
                        kids = node.GetChildren()
                    except Exception:
                        return
                    for ch in kids:
                        if len(found) >= 8:
                            return
                        try:
                            ctc = (getattr(ch, 'ControlTypeName', '')
                                   or '').lower()
                        except Exception:
                            continue
                        if ctc in ("editcontrol", "documentcontrol"):
                            try:
                                nm = ch.Name or ""
                            except Exception:
                                nm = ""
                            try:
                                aid = ch.AutomationId or ""
                            except Exception:
                                aid = ""
                            try:
                                val = ch.GetValuePattern().Value or ""
                            except Exception:
                                val = ""
                            key = ("aid:" + aid.strip()) if aid.strip() \
                                else ("tn:EditControl|%s" % nm)
                            found.append((key, val[:120]))
                        elif ctc in ("checkboxcontrol",
                                     "radiobuttoncontrol"):
                            try:
                                nm = ch.Name or ""
                            except Exception:
                                nm = ""
                            try:
                                aid = ch.AutomationId or ""
                            except Exception:
                                aid = ""
                            try:
                                tog = ch.GetTogglePattern().ToggleState
                                val = "on" if int(tog) == 1 else "off"
                            except Exception:
                                val = ""
                            key = ("aid:" + aid.strip()) if aid.strip() \
                                else ("tn:CheckBoxControl|%s" % nm)
                            found.append((key, val))
                        elif ctc in ("panecontrol", "groupcontrol",
                                     "customcontrol", "windowcontrol",
                                     "tabcontrol"):
                            visit(ch, depth + 1)

                visit(root, 0)
                for key, val in found:
                    out[key] = val
        except Exception:
            pass
        self.metrics.record("value_read_ms",
                            (time.perf_counter() - t0) * 1000.0)
        return out

    def _build_control_sig(self, snap) -> Dict[str, tuple]:
        """Stable-identity signature of one snapshot's controls.

        Identity = AutomationId else (type, name); enabled + name +
        sampled edit value form the compared triple. Rects, timestamps
        and wrapper objects never participate (jitter/churn-proof).
        Edit values are sampled live (capped) because snapshot rows
        carry names, not values.
        """
        try:
            ctrls = snap.controls or []
        except Exception:
            return {}
        sig = {}
        for c in ctrls:
            try:
                sig[_control_identity(c)] = _control_sig_entry(c)
            except Exception:
                continue
        # Sampled values only for edits actually present (cap inside).
        try:
            hwnd = (snap.active_window or {}).get("hwnd", 0) \
                if isinstance(getattr(snap, "active_window", None),
                              dict) else 0
        except Exception:
            hwnd = 0
        if hwnd and any(k.startswith(("tn:EditControl", "tn:DocumentControl",
                                         "tn:CheckBoxControl",
                                         "tn:RadioButtonControl"))
                         or k.startswith("aid:") for k in sig):
            values = self._sample_edit_values(hwnd, ctrls)
            for k, v in values.items():
                if k in sig:
                    en, nm, _old = sig[k]
                    sig[k] = (en, nm, v)
        return sig

    def _on_change(self, cur: Dict[str, Any], change: ChangeClass,
                   reasons: List[str]) -> None:
        """Medium lane: refresh shared cache, classify dialog, emit."""
        from .state import PerceptionLevel
        t0 = time.perf_counter()
        try:
            snap = self.engine.observe(
                PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
        except Exception as e:
            log.warning(f"Perception medium refresh failed: {e}")
            return
        self.metrics.record("uia_refresh_ms",
                            (time.perf_counter() - t0) * 1000.0)
        # Dialog classification on the fresh snapshot.
        dialog_kind = "none"
        t1 = time.perf_counter()
        try:
            from .dialogs import classify_dialog
            dlg = classify_dialog(snap, None)
            dialog_kind = getattr(getattr(dlg, "kind", None), "value",
                                  "none") or "none"
            if dialog_kind not in ("none",):
                change = ChangeClass.RECOVERY_RELEVANT
        except Exception:
            pass
        self.metrics.record("dialog_classify_ms",
                            (time.perf_counter() - t1) * 1000.0)
        # Tree signature (cheap structural fingerprint).
        try:
            ctrls = snap.controls or []
            sig = (len(ctrls), tuple(sorted(
                (c.name or "") for c in ctrls[:40]))),
        except Exception:
            sig = None
        with self._lock:
            if sig is not None and sig == self._last_tree_sig and not reasons:
                pass
            self._last_tree_sig = sig
        win_reasons = [r for r in reasons
                       if r.startswith(("window_appeared:", "window_closed:"))]
        related = "; ".join(win_reasons)
        # Reason signature for the observation key: pure backstop ticks
        # carry no information and must still dedup; every other event
        # kind forces emission so its delta survives.
        rsig = tuple(sorted(set(
            r.split(":", 1)[0] for r in reasons
            if not r.startswith("backstop"))))
        if win_reasons and change in (ChangeClass.IRRELEVANT, ChangeClass.LOW):
            # A window was born or died behind the foreground app — that is
            # action-relevant even when focus never moved.
            change = ChangeClass.ACTION_RELEVANT
        # Control-level delta (§4): same-window control births, deaths,
        # enabled flips, text/value changes. On a window switch the
        # baseline resets WITHOUT emitting a flood (that switch is
        # already reported as fg_changed).
        snap_hwnd = 0
        try:
            snap_hwnd = (snap.active_window or {}).get("hwnd", 0) \
                if isinstance(snap.active_window, dict) else 0
        except Exception:
            pass
        cur_sig = self._build_control_sig(snap)
        csig = _sig_hash(cur_sig)
        with self._lock:
            prev_sig = dict(self._last_control_sig)
            prev_hwnd = getattr(self, "_last_control_hwnd", 0)
            self._last_control_sig = cur_sig
            self._last_control_hwnd = snap_hwnd
            try:
                from collections import Counter as _Ctr
                _counts = _Ctr()
                for _k in cur_sig:
                    _ct = _k.split(":", 1)[0]
                    _counts[_ct] += 1
                self._last_control_summary = "%d controls: %s" % (
                    len(cur_sig), ", ".join(
                        "%d %s" % (v, k) for k, v in
                        sorted(_counts.items())[:8]))
            except Exception:
                self._last_control_summary = "%d controls" % len(cur_sig)
        if prev_hwnd and snap_hwnd and prev_hwnd != snap_hwnd:
            control_delta: Dict[str, str] = {}
        else:
            control_delta = compute_control_delta(prev_sig, cur_sig)
        if control_delta and change in (ChangeClass.IRRELEVANT,
                                        ChangeClass.LOW):
            change = ChangeClass.ACTION_RELEVANT
        # Build controls summary for pattern detection
        try:
            ctrls = snap.controls or []
            control_types = list(set(c.ctype for c in ctrls if getattr(c, 'ctype', '')))
            controls_summary = f"{len(ctrls)} controls: " + ", ".join(
                f"{c.ctype}({c.name[:20]})" for c in ctrls[:10]
            )
        except Exception:
            control_types = []
            controls_summary = ""
        
        obs = SemanticObservation(
            captured_at=time.time(),
            active_app=snap.active_app,
            window_title=(snap.active_window or {}).get("title", "")
            if isinstance(snap.active_window, dict) else "",
            hwnd=(snap.active_window or {}).get("hwnd", 0)
            if isinstance(snap.active_window, dict) else 0,
            wclass=(snap.active_window or {}).get("wclass", "")
            if isinstance(snap.active_window, dict) else "",
            focused_name=(snap.focused_control.name
                          if snap.focused_control else cur.get(
                              "focused_name", "")),
            focused_ctype=(snap.focused_control.ctype
                           if snap.focused_control else cur.get(
                               "focused_ctype", "")),
            dialog_kind=dialog_kind,
            change=change.value,
            summary=(f"{snap.active_app} | "
                     f"{(snap.active_window or {}).get('title', '')[:60] if isinstance(snap.active_window, dict) else ''} "
                     f"| focus={cur.get('focused_name', '')[:40]}"
                     + (f" | {'; '.join(reasons)}" if reasons else "")),
            confidence=0.85 if change is not ChangeClass.IRRELEVANT else 0.2,
            source="event" if reasons else "poll",
            related=related,
            csig=csig,
            controls_summary=controls_summary,
            control_types=control_types,
        )
        with self._lock:
            last = self._last_observation
            if last is not None and last.key() == obs.key():
                self._counters["observations_deduped"] += 1
                return
            obs.delta = compute_scene_delta(last, obs)
            # Menu open/close + window-state transitions arrive as event
            # reasons; surface them as first-class delta keys.
            for r in reasons:
                if r == "menu_opened":
                    obs.delta.setdefault("menu_opened", "menu")
                elif r == "menu_closed":
                    obs.delta.setdefault("menu_closed", "menu")
                elif r.startswith("window_state:"):
                    obs.delta.setdefault(
                        "window_state_changed", r.split(":", 1)[1][:40])
                elif r == "selection":
                    obs.delta.setdefault("selection_changed", "event")
            for k, v in control_delta.items():
                obs.delta.setdefault(k, v)
            self._last_observation = obs
            self._counters["observations_created"] += 1
            self._scene_version += 1
            if dialog_kind not in ("none", ""):
                self._dialog_histogram[dialog_kind] = \
                    self._dialog_histogram.get(dialog_kind, 0) + 1
        # Meaningful change accelerates perception briefly, then decay.
        if change in (ChangeClass.ACTION_RELEVANT,
                      ChangeClass.RECOVERY_RELEVANT,
                      ChangeClass.USER_RELEVANT):
            self.burst(3.0)
        self.observations.push(obs)

    # ---------------- slow lane (explicit only) ----------------

    def observe_now(self, level=None):
        """On-demand deep observation through the existing engine."""
        from .state import PerceptionLevel
        return self.engine.observe(
            level or PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)

    def screen_hash(self, image=None) -> Optional[int]:
        """Cheap perceptual hash of the current (or given) frame."""
        t0 = time.perf_counter()
        try:
            img = image
            if img is None:
                if not self._capture_fn:
                    return None
                img = self._capture_fn()
                if img is None:
                    return None
            from PIL import Image as _PILImage
            import imagehash
            if not isinstance(img, _PILImage.Image):
                from io import BytesIO
                img = _PILImage.open(BytesIO(img))
            h = imagehash.dhash(img.resize((32, 32)))
            self.metrics.record("screen_diff_ms",
                                (time.perf_counter() - t0) * 1000.0)
            return int(str(h), 16)
        except Exception as e:
            log.warning(f"Screen hash failed: {e}")
            return None

    def capture_frame(self, purpose: str):
        """Capture one frame into the TTL ring. Returns PNG bytes or None."""
        if not self._capture_fn:
            return None
        t0 = time.perf_counter()
        try:
            img = self._capture_fn()
            if img is None:
                return None
            from io import BytesIO
            from PIL import Image as _PILImage
            if isinstance(img, _PILImage.Image):
                buf = BytesIO()
                img.save(buf, format="PNG")
                data = buf.getvalue()
            elif isinstance(img, (bytes, bytearray)):
                data = bytes(img)
            else:
                return None
            with self._lock:
                self._counters["frames_seen"] += 1
            expired = self._frames.push(data, purpose)
            self.metrics.record("capture_ms",
                                (time.perf_counter() - t0) * 1000.0)
            with self._lock:
                self._counters["frames_discarded"] += expired
                self._counters["expired_items_deleted"] += expired
            return data
        except Exception as e:
            log.warning(f"Frame capture failed: {e}")
            return None

    def ocr_region(self, box=None):
        """Targeted OCR of one region (or full frame). Returns text/None.

        OCR is a fallback: failure yields 'OCR unavailable', never an
        unknown screen — UIA state stays authoritative.
        """
        t0 = time.perf_counter()
        try:
            img = None
            if self._capture_fn:
                try:
                    img = self._capture_fn()
                except Exception:
                    img = None
            if img is None:
                return None
            from PIL import Image as _PILImage
            from io import BytesIO
            if not isinstance(img, _PILImage.Image):
                img = _PILImage.open(BytesIO(img))
            if box:
                w, h = img.size
                x, y, bw, bh = (max(0, int(box[0])), max(0, int(box[1])),
                                int(box[2]), int(box[3]))
                img = img.crop((x, y, min(w, x + bw), min(h, y + bh)))
            from core.ocr import ocr_image
            text = ocr_image(img) or ""
            self.metrics.record("ocr_ms",
                                (time.perf_counter() - t0) * 1000.0)
            return text
        except Exception as e:
            log.warning(f"Targeted OCR failed: {e}")
            return None

    # ---------------- memory hygiene ----------------

    def remember_observation(self, obs: Optional[SemanticObservation] = None,
                             category: str = "screen_state") -> bool:
        """Persist a STRUCTURED observation. Never pixels, never secrets."""
        if self._memory is None:
            return False
        o = obs or self.current_observation()
        if o is None:
            return False
        fact = o.to_fact()
        if self._sanitizer is not None:
            try:
                fact = self._sanitizer.sanitize(fact)
            except Exception:
                pass
        if any(tok in fact for tok in ("<REDACTED",)):
            pass
        try:
            ok = self._memory.remember_fact(fact, category=category)
            if ok:
                with self._lock:
                    self._counters["persistent_memories_created"] += 1
            return bool(ok)
        except Exception:
            return False

    def current_observation(self) -> Optional[SemanticObservation]:
        with self._lock:
            return self._last_observation

    def counters(self) -> Dict[str, int]:
        with self._lock:
            d = dict(self._counters)
            d["expired_items_deleted"] += self._frames.expired_deleted
            return d

    def metrics_summary(self) -> Dict[str, Dict[str, float]]:
        return self.metrics.summary()

    # ---------------- WinEvent hook ----------------

    def _start_hook_thread(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            # Quick capability probe: hook install itself happens in-thread.
            self._hook_thread = threading.Thread(
                target=self._hook_loop, name="dude-perception-events",
                daemon=True)
            self._hook_thread.start()
        except Exception as e:
            log.warning(f"WinEvent hook unavailable, polling only: {e}")
            self.events_enabled = False

    def _mark_dirty(self, reason: str) -> None:
        with self._lock:
            self._dirty = True
            if all(r != reason for _, r in self._dirty_reasons):
                self._dirty_reasons.append((time.time(), reason))

    def _hook_loop(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)
            try:
                WINEVENTPROC = ctypes.WINFUNCTYPE(
                    None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                    ctypes.c_long, ctypes.c_long, ctypes.c_uint,
                    ctypes.c_uint)

                service_ref = self
                event_kinds = {
                    _WINEVENT_FOREGROUND: "foreground",
                    _WINEVENT_OBJECT_FOCUS: "focus",
                    _WINEVENT_OBJECT_CREATE: "window_created",
                    _WINEVENT_OBJECT_DESTROY: "window_closed",
                    _WINEVENT_MENUSTART: "menu_opened",
                    _WINEVENT_MENUEND: "menu_closed",
                    _WINEVENT_DIALOGSTART: "dialog",
                    # Value/state/selection changes drive the control
                    # delta lane. Bursts coalesce in the dirty flag, so
                    # typing storms cost one refresh, not one per key.
                    _WINEVENT_OBJECT_VALUECHANGE: "value_changed",
                    _WINEVENT_OBJECT_STATECHANGE: "state_changed",
                    _WINEVENT_OBJECT_SELECTION: "selection",
                }

                def _cb(hhook, event, hwnd, _id_obj, _id_child,
                        _tid, _ts):
                    try:
                        kind = event_kinds.get(int(event), "")
                        if kind:
                            service_ref._mark_dirty(kind)
                    except Exception:
                        pass

                cb = WINEVENTPROC(_cb)
                # Keep callback alive for the loop's lifetime.
                self._hook_callbacks.append(cb)
                hook = user32.SetWinEventHook(
                    0x00000001, 0x0000FFFF, None, cb, 0, 0,
                    _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS)
                if not hook:
                    self.events_enabled = False
                    return
                self._hook_handle = hook
                self.events_enabled = True
                msg = wintypes.MSG()
                while not self._stop.is_set():
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret in (0, -1):
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                try:
                    ole32.CoUninitialize()
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"WinEvent hook loop fault (polling continues): {e}")
            self.events_enabled = False
