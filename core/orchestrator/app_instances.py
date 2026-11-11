"""Application instance manager for DUDE Orchestrator (Phase 5).

Answers one question before any launch: is there already a suitable
window for this task, and is it safe to reuse? Decisions are recorded
on the task (window_decisions) so tests and metrics can prove reuse
instead of assuming it.

Reuse policy (default): reuse a suitable existing window; open another
instance only when none exists, when every candidate is unusable, or
when the task explicitly requires independence. Among candidates:
task-owned windows first, then the foreground one when it already
belongs to the task's application, otherwise the lowest HWND (stable,
never random). Never closes anything; closing stays with the caller,
which must prove ownership first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

log = logging.getLogger(__name__)


class ReuseDecision(Enum):
    """Outcome of resolving an application to a window."""
    REUSE_EXISTING = "reuse_existing"
    OPEN_NEW = "open_new"
    UNSAFE_TO_REUSE = "unsafe_to_reuse"
    NOT_FOUND = "not_found"


@dataclass
class WindowInfo:
    """One observed top-level window."""
    hwnd: int = 0
    pid: int = 0
    exe: str = ""
    title: str = ""
    wclass: str = ""
    visible: bool = False
    responsive: Optional[bool] = None  # None = could not determine
    owned_by_task: bool = False
    # None = unknown (API unavailable); False filters the window out of
    # reuse: focusing across virtual desktops does not work, so an
    # off-desktop window is never "suitable", however tempting.
    on_current_desktop: Optional[bool] = None


@dataclass
class ResolveResult:
    """What the manager decided, and why."""
    decision: ReuseDecision = ReuseDecision.NOT_FOUND
    window: Optional[WindowInfo] = None
    reason: str = ""
    candidates_considered: int = 0


# Normalized app name -> process image names. Unknown apps fall back to
# "<name>.exe" so new applications work without code changes.
KNOWN_EXES = {
    "notepad": ["notepad.exe"],
    "file explorer": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "comet": ["comet.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "calc": ["calc.exe"],
    "calculator": ["calc.exe"],
}


# Process image -> window classes that are real application windows.
# explorer.exe hosts the whole shell (Program Manager, taskbar, thumbnail
# helpers); without this filter every one of those counts as "an Explorer
# window", and focusing them fails or misfires. Unknown exes fall back to
# requiring a non-empty title, which excludes most chrome.
KNOWN_CLASSES = {
    "explorer.exe": ["CabinetWClass"],
    # XAML popup hosts (tooltips, suggestion flyouts) share the app
    # process; only the real frame window counts.
    "notepad.exe": ["Notepad"],
    # Calculator UWP app runs as ApplicationFrameHost.exe with
    # ApplicationFrameWindow class
    "applicationframehost.exe": ["ApplicationFrameWindow"],
}


def is_app_window(exe: str, wclass: str, title: str) -> bool:
    """True when a window is a real application window, not shell chrome.

    Shared by production enumeration and test harnesses so both count
    the same windows.
    """
    if exe in KNOWN_CLASSES:
        return wclass in KNOWN_CLASSES[exe]
    return bool((title or "").strip())


# Host processes that tell nothing about the hosted application (UWP/Store
# apps all run under ApplicationFrameHost.exe). A window owned by one of
# these is identified by its TITLE, never the host executable.
UWP_HOST_EXES = {"applicationframehost.exe"}


def _app_base(name) -> str:
    import re as _re
    base = (name or "").lower()
    if base.endswith(".exe"):
        base = base[:-4]
    # Identity folding: voice transcripts ("Notepad.", "note pad") and
    # titles ("*+ Thought 26.5s.txt - Notepad") must all reduce to the
    # same alphanumeric kernel ("notepad") for matching to hold.
    return _re.sub(r'[^a-z0-9]', '', base)


def app_identity_matches(expected_app, observed_exe,
                         observed_title: str = "") -> bool:
    """True when an observed window may be the expected application.

    Canonical app-identity check shared by verification, task-window
    anchoring, and reuse resolution so UWP hosting is handled in exactly
    one place. Normal processes match by executable basename (either
    direction, so "windowsterminal" matches "windowsterminal.exe" and
    "terminal" matches "windowsterminal.exe"). Windows owned by a UWP
    host process match by TITLE instead, because the host executable
    is identical for every Store app. Empty expectation matches
    (nothing asserted); everything else fails closed.
    """
    exp = _app_base(expected_app)
    if not exp:
        return True
    exe = _app_base(observed_exe)
    if exp and (exp in exe or exe in exp):
        return True
    if exe + ".exe" in UWP_HOST_EXES or exe in UWP_HOST_EXES:
        import re as _re
        folded_title = _re.sub(r'[^a-z0-9]', '',
                               (observed_title or "").lower())
        return bool(exp) and exp in folded_title
    return False


def _wanted_window(exe: str, wclass: str, title: str) -> bool:
    return is_app_window(exe, wclass, title)


def _exe_names_for(app_name: str) -> list:
    base = (app_name or "").strip().lower()
    if base in KNOWN_EXES:
        return list(KNOWN_EXES[base])
    cleaned = "".join(c for c in base if c.isalnum() or c in ("-", "_"))
    return [cleaned + ".exe"] if cleaned else []


def _is_hung(hwnd) -> Optional[bool]:
    # NOTE: this lives in user32, not in the win32gui module namespace.
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsHungAppWindow(hwnd))
    except Exception:
        return None


def _on_current_desktop(hwnd) -> Optional[bool]:
    """True when the window sits on the calling thread's virtual desktop.

    None when the VirtualDesktop API is unavailable: callers treat that
    as "unknown", never as a refusal.
    """
    try:
        import pythoncom
        manager = pythoncom.CoCreateInstance(
            pythoncom.MakeIID("{AA509086-5CA9-4C25-8F95-589D3C07B48A}"),
            None,
            pythoncom.CLSCTX_ALL,
            pythoncom.MakeIID("{A5CDABFF-4961-431A-8B48-77C49871406E}"),
        )
        return bool(manager.IsWindowOnCurrentVirtualDesktop(hwnd))
    except Exception:
        return None


class AppInstanceManager:
    """Finds, assesses, and tracks application windows. Read-only except
    for the task's own bookkeeping (owned sets, decision records)."""

    def snapshot(self, app_names=None) -> list:
        """List visible top-level windows, optionally filtered by app."""
        wanted = None
        wanted_bases = set()
        if app_names:
            if isinstance(app_names, str):
                app_names = [app_names]
            wanted = set()
            for name in app_names:
                wanted.update(_exe_names_for(name))
            # Base names double as title hints for UWP-hosted windows
            # (e.g. "calc" matches a hosted window titled "Calculator").
            wanted_bases = {_app_base(w) for w in wanted if _app_base(w)}
        windows = []
        try:
            import win32gui
            import win32process
            import psutil
        except Exception:
            return windows

        def cb(hwnd, acc):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    exe = (psutil.Process(pid).name() or "").lower()
                except Exception:
                    return True
                if wanted is not None and exe not in wanted:
                    # UWP-hosted windows never match by executable (the
                    # host is identical for every Store app); match by
                    # title against the wanted app names instead, so a
                    # running Calculator is reusable instead of invisible.
                    if exe in UWP_HOST_EXES:
                        try:
                            _t = win32gui.GetWindowText(hwnd) or ""
                        except Exception:
                            _t = ""
                        if not any(b and b in _t.lower()
                                   for b in wanted_bases):
                            return True
                    else:
                        return True
                try:
                    title = win32gui.GetWindowText(hwnd) or ""
                except Exception:
                    title = ""
                try:
                    wclass = win32gui.GetClassName(hwnd) or ""
                except Exception:
                    wclass = ""
                if not is_app_window(exe, wclass, title):
                    return True
                hung = _is_hung(hwnd)
                acc.append(WindowInfo(
                    hwnd=hwnd, pid=pid, exe=exe, title=title,
                    wclass=wclass, visible=True,
                    responsive=(not hung) if hung is not None else None,
                    on_current_desktop=_on_current_desktop(hwnd),
                ))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(cb, windows)
        except Exception:
            pass
        return windows

    def resolve(self, app_name: str, task_state=None,
                require_independent: bool = False) -> ResolveResult:
        """Decide whether to reuse an existing window for a task."""
        if require_independent:
            return ResolveResult(
                decision=ReuseDecision.OPEN_NEW,
                reason="task explicitly requires an independent window",
            )
        candidates = self.snapshot([app_name])
        owned = set()
        current_app = ""
        if task_state is not None:
            owned = set(getattr(task_state, "owned_hwnds", set()) or set())
            current_app = (getattr(task_state, "current_application", "")
                           or "").lower()
        usable = [w for w in candidates
                  if w.responsive is not False
                  and w.on_current_desktop is not False]
        for w in usable:
            w.owned_by_task = w.hwnd in owned
        if not candidates:
            return ResolveResult(
                decision=ReuseDecision.NOT_FOUND,
                reason=f"no {app_name} window exists",
                candidates_considered=0,
            )
        if not usable:
            return ResolveResult(
                decision=ReuseDecision.UNSAFE_TO_REUSE,
                reason=(f"{len(candidates)} {app_name} window(s) exist "
                        f"but all are unresponsive or off-desktop"),
                candidates_considered=len(candidates),
            )
        # 1. windows this task already owns (it created them).
        for w in sorted(usable, key=lambda w: w.hwnd):
            if w.owned_by_task:
                return ResolveResult(
                    decision=ReuseDecision.REUSE_EXISTING,
                    window=w,
                    reason="reusing task-owned window",
                    candidates_considered=len(candidates),
                )
        # 2. the foreground window when it already serves this task's app.
        try:
            import win32gui
            fg = win32gui.GetForegroundWindow()
            for w in usable:
                if w.hwnd == fg and current_app and app_identity_matches(
                        current_app, w.exe, w.title):
                    return ResolveResult(
                        decision=ReuseDecision.REUSE_EXISTING,
                        window=w,
                        reason="reusing foreground window of task app",
                        candidates_considered=len(candidates),
                    )
        except Exception:
            pass
        # 3. stable fallback: lowest HWND (deterministic, never random).
        w = sorted(usable, key=lambda w: w.hwnd)[0]
        return ResolveResult(
            decision=ReuseDecision.REUSE_EXISTING,
            window=w,
            reason="reusing lowest-HWND suitable window",
            candidates_considered=len(candidates),
        )

    @staticmethod
    def record(task_state, app: str, result: ResolveResult) -> None:
        """Append a window decision to the task for metrics and tests."""
        if task_state is None:
            return
        try:
            decisions = getattr(task_state, "window_decisions", None)
            if decisions is None:
                return
            decisions.append({
                "phase": "window_resolve",
                "app": app,
                "decision": result.decision.value,
                "hwnd": result.window.hwnd if result.window else None,
                "reason": result.reason,
            })
        except Exception:
            pass

    @staticmethod
    def mark_owned(task_state, hwnd: int) -> None:
        """Record a window this task created (only these may be closed)."""
        if task_state is None or not hwnd:
            return
        try:
            owned = getattr(task_state, "owned_hwnds", None)
            if owned is not None:
                owned.add(hwnd)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background capability: evidence-based, per application.
# ---------------------------------------------------------------------------

# Mechanisms proven (or explicitly probed) in-repo. Anything absent here
# is classified by conservative default, never assumed.
BACKGROUND_EVIDENCE = {
    # UIA ValuePattern read proven by buffer audits; writes untested.
    "notepad.exe": {"uia_value_read": "supported",
                    "uia_value_write": "untested"},
    # Keyboard/click-driven flows require focus (Phase 3/4 evidence).
    "explorer.exe": {"keyboard": "foreground_only"},
    "comet.exe": {"keyboard": "foreground_only", "click": "foreground_only"},
}


def classify_background(exe_name: str) -> dict:
    """Classify what a target application supports without focus.

    Returns {"target", "read_without_focus", "write_without_focus",
    "verdict", "evidence"}. Verdicts: BACKGROUND_READ_OK (perception
    never needs focus), WRITE_UNTESTED (must be probed live, never
    assumed), FOREGROUND_ONLY (proven focus-dependent). Unknown apps
    get FOREGROUND_ONLY: the safe default.
    """
    exe = (exe_name or "").lower()
    if exe.endswith(".exe"):
        base = exe[:-4]
    else:
        base = exe
        exe = exe + ".exe" if exe else exe
    known = BACKGROUND_EVIDENCE.get(exe) or BACKGROUND_EVIDENCE.get(base)
    if known is None:
        return {"target": exe or base or "unknown",
                "read_without_focus": True,
                "write_without_focus": "untested",
                "verdict": "FOREGROUND_ONLY",
                "evidence": "no background evidence on record; "
                            "foreground input required until probed"}
    if known.get("uia_value_write") == "supported":
        verdict = "BACKGROUND_WRITE_OK"
    elif known.get("uia_value_write") == "untested":
        verdict = "WRITE_UNTESTED"
    else:
        verdict = "FOREGROUND_ONLY"
    return {"target": exe,
            "read_without_focus": True,
            "write_without_focus": known.get("uia_value_write",
                                             "unsupported"
                                             if verdict == "FOREGROUND_ONLY"
                                             else "untested"),
            "verdict": verdict,
            "evidence": "in-repo evidence table + live probes"}
