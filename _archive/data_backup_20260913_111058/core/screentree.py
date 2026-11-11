"""Continuous structured screen map via Windows UI Automation.

Screenshots + free-tier vision models cannot reliably find buttons and their
coordinates. Instead, this module reads the ACTUAL accessibility/UI tree of
the foreground window: every visible button, text box, tab, checkbox and menu
keeps its real NAME and exact PIXEL position, refreshed continuously by a
background thread. That gives DUDE a deterministic, always-current "mind's eye"
of the screen it can act on with the cursor -- no OCR, no image understanding.
"""
import sys
import threading
import time

try:
    import uiautomation as auto
    _UIA = True
except Exception:  # pragma: no cover
    _UIA = False

_INTERACTIVE = {
    "ButtonControl", "EditControl", "TabItemControl", "ComboBoxControl",
    "CheckBoxControl", "RadioButtonControl", "HyperlinkControl",
    "ListItemControl", "MenuItemControl", "TreeItemControl", "SliderControl",
    "SplitButtonControl", "SpinnerControl", "ToggleButtonControl",
    "TitleBarControl", "ToolBarControl",
}
_ROLE_SHORT = {
    "ButtonControl": "btn", "EditControl": "input", "TabItemControl": "tab",
    "ComboBoxControl": "dropdown", "CheckBoxControl": "check",
    "RadioButtonControl": "radio", "HyperlinkControl": "link",
    "ListItemControl": "item", "MenuItemControl": "menu",
    "TreeItemControl": "tree-item", "SliderControl": "slider",
    "SplitButtonControl": "split-btn", "SpinnerControl": "spin",
    "ToggleButtonControl": "toggle", "TitleBarControl": "titlebar",
    "ToolBarControl": "toolbar", "PaneControl": "pane", "GroupControl": "group",
    "TextControl": "text", "WindowControl": "window",
}


def _row_text(rec):
    role = _ROLE_SHORT.get(rec.get("ctype", ""), rec.get("ctype", ""))
    x, y, w, h = rec["x"], rec["y"], rec["w"], rec["h"]
    name = rec.get("name") or ""
    if len(name) > 60:
        name = name[:60] + "…"
    return f'[{role}] "{name}" @ ({x},{y},{w}x{h})'


class ScreenMap:
    def __init__(self, on_change=None):
        self._rows = []
        self._app = "unknown"
        self._title = "unknown"
        self._handle = None
        self._built_at = 0.0
        self._focused = ""
        self._focused_at = 0.0
        self._mouse = (0, 0)
        self._recent_clicks = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._on_change = on_change
        if _UIA:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="du-screentree")
            self._thread.start()

    # ---------------- public reads ----------------

    def available(self):
        return _UIA

    @property
    def rows(self):
        with self._lock:
            return list(self._rows)

    def find(self, name=None, role=None):
        """Return the (top) control row matching a name substring (and/or a
        short role like 'btn', 'input', 'tab')."""
        if role:
            role = role.lower()
        name_l = (name or "").lower()
        with self._lock:
            rows = list(self._rows)
            if name_l:
                rows = [r for r in rows if name_l in (r.get("name") or "").lower()]
            if role:
                short = _ROLE_SHORT.get(rows[0].get("ctype") if rows else "", "")
                rows = [r for r in rows if
                        _ROLE_SHORT.get(r.get("ctype"), r.get("ctype")).lower() == role or
                        r.get("ctype", "").lower() == role]
        return rows[0] if rows else None

    def current_view(self, limit=26):
        with self._lock:
            app, title = self._app, self._title
            rows = list(self._rows)
            focused = self._focused
            mouse = self._mouse
            clicks = list(self._recent_clicks)
        out = [f"ACTIVE APP: {app} — {title}"
               f" | CURSOR: ({mouse[0]},{mouse[1]})"]
        if focused:
            out[0] += f" | FOCUSED: {focused}"
        parts = [_row_text(r) for r in rows[:limit]]
        if parts:
            out.append("CONTROLS ON SCREEN (name, type, position):")
            out.extend(f"{i+1}. {p}" for i, p in enumerate(parts))
        if rows and len(rows) > limit:
            out.append(f"... {len(rows) - limit} more controls")
        if clicks:
            out.append("RECENT CLICKS: " + "; ".join(clicks[-3:]))
        return "\n".join(out)

    def note_click(self, text):
        with self._lock:
            self._recent_clicks.append(text)
            self._recent_clicks = self._recent_clicks[-6:]

    # ---------------- background loop ----------------

    def _loop(self):
        while not self._stop.wait(2.0):
            try:
                if _UIA:
                    with auto.UIAutomationInitializerInThread():
                        self._tick()
            except Exception:
                pass

    def stop(self):
        self._stop.set()

    def _tick(self):
        try:
            fg = auto.GetForegroundControl()
        except Exception:
            return
        if fg is None:
            return
        handle = getattr(fg, "NativeWindowHandle", None)
        now = time.time()
        rebuild = (self._handle != handle) or (now - self._built_at > 20.0)
        if rebuild:
            rows, app, title = self._walk(fg)
            with self._lock:
                self._rows = rows
                self._app = app
                self._title = title
                self._handle = handle
                self._built_at = now
            cb = self._on_change
            if cb:
                try:
                    cb(app, title)
                except Exception:
                    pass
        if now - self._focused_at > 5.0:
            focus = ""
            try:
                fc = auto.GetFocusedControl()
                if fc is not None:
                    focus = ((fc.Name or "") + " " + _ROLE_SHORT.get(
                        getattr(fc, "ControlTypeName", ""), "")).strip()[:60]
            except Exception:
                pass
            with self._lock:
                self._focused = focus
                self._focused_at = now
        try:
            pos = auto.GetCursorPos()
        except Exception:
            pos = self._mouse
        if pos:
            with self._lock:
                self._mouse = (int(pos[0]), int(pos[1]))

    def _walk(self, fg):
        try:
            ctype = getattr(fg, "ControlTypeName", "WindowControl")
            rows = [{
                "ctype": ctype,
                "name": fg.Name or "",
                "x": 0, "y": 0, "w": 0, "h": 0,
                "enabled": True,
            }]
        except Exception:
            rows = []
        seen = set()
        try:
            root_name = (fg.Name or "")[:80]
            pid = 0
            try:
                pid = fg.ProcessId
            except Exception:
                pass
            app = "unknown"
            try:
                import psutil
                n = psutil.Process(pid).name()
                if n:
                    app = n.rsplit(".", 1)[0]
            except Exception:
                pass
            depth_limit = 8
            def visit(ctrl, depth):
                if len(rows) >= 220 or depth > depth_limit:
                    return
                for ch in ctrl.GetChildren():
                    if len(rows) >= 220:
                        return
                    try:
                        name = ch.Name or ""
                        ctc = getattr(ch, "ControlTypeName", "") or ""
                        rect = ch.BoundingRectangle
                        l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
                        if r <= l or b <= t:
                            continue
                        try:
                            off = ch.IsOffscreen
                        except Exception:
                            off = False
                        if off:
                            continue
                        try:
                            enabled = ch.IsEnabled
                        except Exception:
                            enabled = True
                        interactive = ctc in _INTERACTIVE
                        if not name and not interactive:
                            continue
                        name_n = " ".join(name.split())
                        if not name_n and not interactive:
                            continue
                        key = (ctc, name_n[:60], int(l), int(t))
                        if key in seen:
                            continue
                        seen.add(key)
                        w, h = int(r - l), int(b - t)
                        rows.append({
                            "ctype": ctc, "name": name_n, "x": int(l), "y": int(t),
                            "w": w, "h": h, "enabled": bool(enabled),
                        })
                        if ctc in ("PaneControl", "GroupControl", "ToolBarControl",
                                   "ComboBoxControl", "TitleBarControl", "TabControl",
                                   "TreeControl", "CustomControl", "WindowControl"):
                            visit(ch, depth + 1)
                        elif ctc == "EditControl" and depth < 3:
                            visit(ch, depth + 1)
                    except Exception:
                        continue
            visit(fg, 0)
        except Exception:
            pass
        return rows, app, (fg.Name or "unknown")[:90]


def _sc_map():
    return globals().get("_MAP")


def set_global_map(m):
    globals()["_MAP"] = m