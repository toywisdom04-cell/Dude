"""Perception foundation for the DUDE Orchestrator.

Provides a clean interface for the perception hierarchy (Level 1-5).
Integrates existing perception modules (observer, experience, screentree, OCR, tracker).
No vision model, no cloud calls, no new capture loops.
"""
from __future__ import annotations

import time
import threading
import base64
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Callable, List

from .state import (
    PerceptionSnapshot,
    ControlInfo,
    OCRRegion,
    ScreenDelta,
    Rect,
    PerceptionLevel,
)


@dataclass
class PerceptionCache:
    """Single source of truth for current perception state."""
    # Level 1
    active_app: str = "unknown"
    active_window: dict = field(default_factory=dict)
    window_bounds: Optional[Rect] = None

    # Level 2
    uia_tree: Optional[list[ControlInfo]] = None
    controls: list[ControlInfo] = field(default_factory=list)
    focused_control: Optional[ControlInfo] = None

    # Level 3
    ocr_text: str = ""
    ocr_regions: list[OCRRegion] = field(default_factory=list)
    ocr_age: float = 0.0

    # Level 4
    screenshot: Optional[bytes] = None
    screenshot_age: float = 0.0
    change_detected: bool = False
    changed_regions: list[Rect] = field(default_factory=list)

    # Level 5
    vision_analysis: Optional[str] = None
    vision_age: float = 0.0

    # Metadata
    captured_at: float = 0.0
    capture_method: PerceptionLevel = PerceptionLevel.LEVEL_1_APP_WINDOW
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)
            self.captured_at = time.time()

    def get_snapshot(self) -> "PerceptionSnapshot":
        with self._lock:
            return PerceptionSnapshot(
                active_app=self.active_app,
                active_window=dict(self.active_window),
                window_bounds=self.window_bounds,
                uia_tree=list(self.controls) if self.controls else None,
                controls=list(self.controls),
                focused_control=self.focused_control,
                ocr_text=self.ocr_text,
                ocr_regions=list(self.ocr_regions),
                screenshot=self.screenshot,
                screenshot_age=time.time() - self.captured_at if self.captured_at else 0.0,
                change_detected=self.change_detected,
                changed_regions=list(self.changed_regions),
                vision_analysis=self.vision_analysis,
                vision_age=time.time() - self.captured_at if self.vision_age else 0.0,
                captured_at=time.time(),
                capture_method=self.capture_method,
                age_seconds=time.time() - self.captured_at if self.captured_at else 0.0,
            )

    def is_fresh(self, max_age: float = 5.0) -> bool:
        return (time.time() - self.captured_at) <= max_age if self.captured_at else False


class PerceptionEngine:
    """Main perception interface for the orchestrator.

    Integrates existing modules (observer, experience, screentree, OCR, tracker)
    into a unified perception hierarchy with lazy level escalation.
    """

    def __init__(
        self,
        get_observer: Optional[Callable] = None,
        get_experience: Optional[Callable] = None,
        get_screentree: Optional[Callable] = None,
        get_tracker: Optional[Callable] = None,
        get_ocr_fn: Optional[Callable] = None,
        get_capture_fn: Optional[Callable] = None,
        config: Optional[Any] = None,
    ):
        self._observer = get_observer
        self._experience = get_experience
        self._screentree = get_screentree
        self._tracker = get_tracker
        self._ocr_fn = get_ocr_fn
        self._capture_fn = get_capture_fn
        self._config = config
        self._cache = PerceptionCache()
        self._previous_screenshot: Optional[bytes] = None
        self._vision_budget_used = 0
        self._vision_budget_limit = 150
        self._lock = threading.Lock()

    def observe(
        self,
        required_level: PerceptionLevel = PerceptionLevel.LEVEL_2_UIA_TREE,
        force_refresh: bool = False,
    ) -> "PerceptionSnapshot":
        """Obtain a fresh perception snapshot up to the required level.

        Captures hierarchy: L1 -> L2 -> L3 -> L4 -> L5 (stops when sufficient).
        Uses lazy escalation - only captures what's needed.
        """
        with self._lock:
            cache = self._cache

            # A forced observation must not serve a stale UIA tree: ask
            # the live screen map for a synchronous rebuild first (best
            # effort; cached rows are used if the rebuild is unavailable).
            if force_refresh and required_level >= PerceptionLevel.LEVEL_2_UIA_TREE:
                try:
                    st = self._screentree() if self._screentree else None
                    if st is not None and hasattr(st, "refresh_sync"):
                        st.refresh_sync()
                except Exception:
                    pass

            # Level 1: App/Window identity (always)
            if force_refresh or not cache.active_app or cache.active_app == "unknown":
                self._capture_level_1()

            if required_level <= PerceptionLevel.LEVEL_1_APP_WINDOW:
                return cache.get_snapshot()

            # Level 2: UI Automation tree
            if force_refresh or not cache.controls:
                self._capture_level_2()

            if required_level <= PerceptionLevel.LEVEL_2_UIA_TREE:
                return cache.get_snapshot()

            # Level 3: OCR
            if force_refresh or not cache.ocr_text or cache.ocr_age > 10.0:
                self._capture_level_3()

            if required_level <= PerceptionLevel.LEVEL_3_OCR:
                return cache.get_snapshot()

            # Level 4: Targeted CV / Screenshot
            if force_refresh or not cache.screenshot or cache.screenshot_age > 30.0:
                self._capture_level_4()

            if required_level <= PerceptionLevel.LEVEL_4_TARGETED_CV:
                return cache.get_snapshot()

            # Level 5: Vision model (interface only - not implemented)
            if required_level <= PerceptionLevel.LEVEL_5_VISION_MODEL:
                self._capture_level_5()

            return cache.get_snapshot()

    def capture_now(self, required_level: PerceptionLevel = PerceptionLevel.LEVEL_2_UIA_TREE) -> "PerceptionSnapshot":
        """Request a fresh perception snapshot immediately.

        Safe for TaskEngine to call before an action. No background thread created.
        """
        return self.observe(required_level=required_level, force_refresh=True)

    def observe_window(self, hwnd: int,
                       required_level: PerceptionLevel = PerceptionLevel.LEVEL_2_UIA_TREE,
                       ) -> "PerceptionSnapshot":
        """Observe one specific window WITHOUT focusing it.

        The primitive behind background operation: UIA tree reads work
        against any window, so perception (and focus-free actions built
        on it, like text reads) can target a pinned window while the
        user keeps the foreground. Screenshots, OCR, and focused-control
        state are foreground-screen evidence and are therefore left
        empty here by design, never faked.
        """
        import time as _time
        title, wclass, exe = "", "", "unknown"
        try:
            import win32gui
            import win32process
            import psutil
            if hwnd and win32gui.IsWindow(hwnd):
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
        except Exception:
            pass
        controls: list = []
        if required_level >= PerceptionLevel.LEVEL_2_UIA_TREE:
            try:
                st = self._screentree() if self._screentree else None
                rows = st.snapshot_hwnd(hwnd) if st is not None and hasattr(
                    st, "snapshot_hwnd") else []
                for r in rows:
                    controls.append(ControlInfo(
                        ctype=r.get("ctype", ""),
                        name=r.get("name", ""),
                        automation_id=r.get("automation_id", ""),
                        x=r.get("x", 0),
                        y=r.get("y", 0),
                        w=r.get("w", 0),
                        h=r.get("h", 0),
                        enabled=r.get("enabled", True),
                        visible=True,
                        role=r.get("ctype", ""),
                    ))
            except Exception:
                pass
        now = _time.time()
        return PerceptionSnapshot(
            active_app=exe,
            active_window={"title": title, "app": exe, "hwnd": hwnd,
                           "wclass": wclass},
            window_bounds=None,
            uia_tree=list(controls),
            controls=controls,
            focused_control=None,
            ocr_text="",
            ocr_regions=[],
            captured_at=now,
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
            age_seconds=0.0,
        )

    # --- Level implementations ---

    @staticmethod
    def _fg_identity():
        """Best-effort foreground window identity (hwnd + window class).

        Dialog classification needs more than app/title: common dialogs
        surface as #32770-owned popups, which only hwnd/class reveal.
        """
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None, ""
            try:
                wclass = win32gui.GetClassName(hwnd)
            except Exception:
                wclass = ""
            return hwnd, wclass or ""
        except Exception:
            return None, ""

    def _capture_level_1(self) -> None:
        """Capture active app/window identity (L1)."""
        # Try observer first (has cached state, cheaper)
        observer = self._observer() if self._observer else None
        if observer:
            try:
                screen = observer.current_screen()
                if screen:
                    bounds = None
                    try:
                        import win32gui
                        hwnd = __import__("win32gui").GetForegroundWindow()
                        if hwnd:
                            rect = win32gui.GetWindowRect(hwnd)
                            bounds = Rect(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
                    except Exception:
                        pass
                    fg_hwnd, fg_class = self._fg_identity()

                    # Also get experience context if available
                    exp_context = None
                    if self._experience:
                        try:
                            exp = self._experience()
                            if exp and hasattr(exp, 'watch_status'):
                                status = exp.watch_status()
                                if status.get("on"):
                                    exp_context = {
                                        "observation_active": True,
                                        "insights_count": status.get("insights", 0),
                                        "frames": status.get("frames", 0),
                                    }
                        except Exception:
                            pass

                    self._cache.update(
                        active_app=screen.get("app", "unknown"),
                        active_window={
                            "title": screen.get("title", "unknown"),
                            "app": screen.get("app", "unknown"),
                            "experience_context": exp_context,
                            "hwnd": fg_hwnd,
                            "wclass": fg_class,
                        },
                        window_bounds=bounds,
                        capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
                    )
                    return
            except Exception:
                pass
        
        # Fallback: direct platform call
        try:
            from platform_utils import get_platform
            platform = get_platform()
            fg = platform.foreground_window()
            bounds = None
            try:
                import win32gui
                hwnd = __import__("win32gui").GetForegroundWindow()
                if hwnd:
                    rect = win32gui.GetWindowRect(hwnd)
                    bounds = Rect(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
            except Exception:
                pass

            fg_hwnd, fg_class = self._fg_identity()
            if isinstance(fg, dict):
                fg = dict(fg)
                fg.setdefault("hwnd", fg_hwnd)
                fg.setdefault("wclass", fg_class)

            self._cache.update(
                active_app=fg.get("app", "unknown"),
                active_window=fg,
                window_bounds=bounds,
                capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
            )
        except Exception:
            pass

    def _capture_level_2(self) -> None:
        """Capture UI Automation tree via screentree (L2)."""
        try:
            if self._screentree:
                st = self._screentree()
                if st and st.available():
                    # Ensure the screentree has fresh data
                    if not st.rows:
                        st.refresh_sync()
                    rows = st.rows
                    controls = []
                    focused = None

                    for r in rows:
                        ctrl = ControlInfo(
                            ctype=r.get("ctype", ""),
                            name=r.get("name", ""),
                            automation_id=r.get("automation_id", ""),
                            x=r.get("x", 0),
                            y=r.get("y", 0),
                            w=r.get("w", 0),
                            h=r.get("h", 0),
                            enabled=r.get("enabled", True),
                            visible=True,
                            role=r.get("ctype", ""),
                        )
                        controls.append(ctrl)

                    # Try to get focused control from screentree
                    # (row dict -> ControlInfo, same shape as tree rows).
                    if hasattr(st, "find_focused"):
                        try:
                            frow = st.find_focused()
                        except Exception:
                            frow = None
                        if isinstance(frow, dict) and frow.get("ctype"):
                            focused = ControlInfo(
                                ctype=frow.get("ctype", ""),
                                name=frow.get("name", ""),
                                automation_id=frow.get("automation_id", ""),
                                x=frow.get("x", 0),
                                y=frow.get("y", 0),
                                w=frow.get("w", 0),
                                h=frow.get("h", 0),
                                enabled=frow.get("enabled", True),
                                visible=True,
                                role=frow.get("ctype", ""),
                            )
                        elif isinstance(frow, ControlInfo):
                            focused = frow
                        else:
                            focused = None
                    else:
                        focused = None

                    self._cache.update(
                        controls=controls,
                        focused_control=focused,
                        uia_tree=controls,
                        capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
                    )
        except Exception:
            pass
        
        # Also integrate observer UIA info if available
        observer = self._observer() if self._observer else None
        if observer and not self._cache.controls:
            try:
                screen = observer.current_screen()
                # Observer doesn't provide full UIA tree, but we can note it has UIA capability
                pass
            except Exception:
                pass

    def _capture_level_3(self) -> None:
        """Capture OCR text via tesseract (L3)."""
        # Try observer first (may have cached OCR)
        observer = self._observer() if self._observer else None
        if observer:
            try:
                screen = observer.current_screen()
                ocr_text = screen.get("ocr", "")
                ocr_age_min = screen.get("ocr_age_min")
                if ocr_text and ocr_age_min is not None and ocr_age_min < 5:
                    # Use observer's cached OCR if fresh (< 5 min)
                    self._cache.update(
                        ocr_text=ocr_text,
                        ocr_regions=[],  # Observer doesn't provide regions
                        ocr_age=ocr_age_min * 60.0,
                        capture_method=PerceptionLevel.LEVEL_3_OCR,
                    )
                    return
            except Exception:
                pass
        
        # Fallback: direct OCR call
        try:
            if self._ocr_fn:
                result = self._ocr_fn()
                if result:
                    regions = [
                        OCRRegion(
                            text=r.get("text", ""),
                            x=r.get("x", 0),
                            y=r.get("y", 0),
                            w=r.get("w", 0),
                            h=r.get("h", 0),
                            confidence=r.get("confidence", 0.0),
                        )
                        for r in result.get("regions", [])
                    ]
                    self._cache.update(
                        ocr_text=result.get("text", ""),
                        ocr_regions=regions,
                        ocr_age=0.0,
                        capture_method=PerceptionLevel.LEVEL_3_OCR,
                    )
        except Exception:
            pass

    def _capture_level_4(self) -> None:
        """Capture screenshot for CV/template matching (L4)."""
        # Try observer first (may have cached screenshot/description)
        observer = self._observer() if self._observer else None
        if observer:
            try:
                screen = observer.current_screen()
                shot = screen.get("shot", "")
                desc = screen.get("description", "")
                desc_age = screen.get("description_age_min")
                if shot and desc and desc_age is not None and desc_age < 10:
                    # Use observer's cached screenshot if fresh
                    import base64
                    screenshot_bytes = base64.b64decode(shot) if isinstance(shot, str) else shot
                    
                    change_detected = False
                    changed_regions = []
                    if self._previous_screenshot:
                        change_detected = self._detect_change(
                            self._previous_screenshot, screenshot_bytes
                        )

                    self._cache.update(
                        screenshot=screenshot_bytes,
                        screenshot_age=desc_age * 60.0,
                        change_detected=change_detected,
                        changed_regions=changed_regions,
                        capture_method=PerceptionLevel.LEVEL_4_TARGETED_CV,
                    )
                    self._previous_screenshot = screenshot_bytes
                    return
            except Exception:
                pass
        
        # Fallback: direct screenshot capture
        try:
            if self._capture_fn:
                img = self._capture_fn()
                if img:
                    # Convert to bytes for storage/comparison
                    from io import BytesIO
                    buf = BytesIO()
                    img.save(buf, format="PNG")
                    screenshot_bytes = buf.getvalue()

                    # Change detection
                    change_detected = False
                    changed_regions = []
                    if self._previous_screenshot:
                        change_detected = self._detect_change(
                            self._previous_screenshot, screenshot_bytes
                        )

                    self._cache.update(
                        screenshot=screenshot_bytes,
                        screenshot_age=0.0,
                        change_detected=change_detected,
                        changed_regions=changed_regions,
                        capture_method=PerceptionLevel.LEVEL_4_TARGETED_CV,
                    )
                    self._previous_screenshot = screenshot_bytes
        except Exception:
            pass

    def _detect_change(self, before: bytes, after: bytes) -> bool:
        """Simple perceptual hash change detection."""
        try:
            from PIL import Image
            from io import BytesIO
            import imagehash

            img1 = Image.open(BytesIO(before))
            img2 = Image.open(BytesIO(after))
            h1 = imagehash.dhash(img1.resize((32, 32)))
            h2 = imagehash.dhash(img2.resize((32, 32)))
            diff = h1 - h2
            return diff > 10
        except Exception:
            return False

    def _capture_level_5(self) -> None:
        """Vision model analysis (interface only - not implemented)."""
        # Vision model would be called here if budget permits
        # For Phase 2A, we explicitly do NOT implement this
        pass

    # --- Control lookup (grounding) ---

    def get_control(self, target_spec) -> Optional["ControlInfo"]:
        """Find a control matching the target specification.

        Priority: UIA (L2) -> UIA role/context -> OCR text (L3) -> CV template (L4) -> coords (L4/L5).
        """
        # Try UIA by automation_id (highest confidence)
        if target_spec.control_id:
            for ctrl in self._cache.controls:
                if ctrl.automation_id == target_spec.control_id and ctrl.enabled and ctrl.visible:
                    return ctrl

        # Try UIA by control_name
        if target_spec.control_name:
            matches = [
                c for c in self._cache.controls
                if target_spec.control_name.lower() in c.name.lower() and c.enabled and c.visible
            ]
            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                # Try to disambiguate by role
                if target_spec.control_role:
                    role_matches = [m for m in matches if m.role == target_spec.control_role]
                    if len(role_matches) == 1:
                        return role_matches[0]

        # Try OCR text match (Level 3)
        if target_spec.text_match:
            for region in self._cache.ocr_regions:
                if target_spec.text_match.lower() in region.text.lower():
                    return ControlInfo(
                        name=region.text,
                        x=region.x,
                        y=region.y,
                        w=region.w,
                        h=region.h,
                        ctype="ocr_text",
                    )

        # Try relative coordinates (Level 4)
        if target_spec.relative_coords and self._cache.window_bounds:
            wb = self._cache.window_bounds
            rx, ry = target_spec.relative_coords
            x = int(wb.x + rx * wb.w)
            y = int(wb.y + ry * wb.h)
            return ControlInfo(
                x=x, y=y, w=1, h=1, ctype="relative_coords",
            )

        # Absolute coordinates (last resort)
        if target_spec.coordinates:
            return ControlInfo(
                x=target_spec.coordinates[0],
                y=target_spec.coordinates[1],
                w=1, h=1, ctype="absolute_coords",
            )

        return None

    # --- Change detection ---

    def detect_change(self, previous: Optional["PerceptionSnapshot"]) -> "ScreenDelta":
        """Compute what changed since the previous perception."""
        current = self._cache.get_snapshot()
        delta = ScreenDelta(timestamp=time.time())

        if not previous:
            delta.app_changed = True
            delta.window_changed = True
            delta.uia_tree_changed = True
            delta.ocr_changed = True
            delta.screenshot_diff = 1.0
            return delta

        delta.app_changed = current.active_app != previous.active_app
        delta.window_changed = current.active_window != previous.active_window
        delta.ocr_changed = current.ocr_text != previous.ocr_text
        delta.screenshot_diff = self._cache.screenshot_age

        # UIA tree diff
        prev_uia = previous.uia_tree or []
        curr_uia = current.uia_tree or []
        if prev_uia or curr_uia:
            prev_names = {c.name for c in prev_uia}
            curr_names = {c.name for c in curr_uia}
            delta.new_controls = [c for c in curr_uia if c.name not in prev_names]
            delta.removed_controls = [c for c in prev_uia if c.name not in curr_names]
            delta.uia_tree_changed = bool(delta.new_controls or delta.removed_controls)

        # Screenshot change detection
        if previous.screenshot and current.screenshot:
            try:
                from PIL import Image
                from io import BytesIO
                import imagehash
                img1 = Image.open(BytesIO(previous.screenshot))
                img2 = Image.open(BytesIO(current.screenshot))
                h1 = imagehash.dhash(img1.resize((32, 32)))
                h2 = imagehash.dhash(img2.resize((32, 32)))
                diff = h1 - h2
                delta.screenshot_diff = float(diff)
                delta.changed_regions = []  # Could be expanded with actual diff regions
                delta.change_detected = diff > 10
            except Exception:
                delta.screenshot_diff = self._cache.screenshot_age
        else:
            delta.screenshot_diff = self._cache.screenshot_age

        return delta

    # --- Public API ---

    @property
    def cache(self) -> PerceptionCache:
        return self._cache

    def can_use_vision(self) -> bool:
        """Check if vision budget allows a call."""
        return self._vision_budget_used < self._vision_budget_limit

    def record_vision_use(self) -> None:
        self._vision_budget_used += 1