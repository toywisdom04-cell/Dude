"""Tk overlay backend for the spatial workspace (Windows).

Safety-first design (after a live incident where an unverified overlay
window ate mouse clicks):

- NO window exists until the first show request (never at startup).
- A window is kept only when click-through (WS_EX_TRANSPARENT) is
  VERIFIED by reading the style back. Anything else destroys the
  window and the backend reports down (callers degrade to verbal).
- The window is topmost, never activated, owned by a daemon thread.
  All Tk calls happen on that thread via a command queue.
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)

_TRANSPARENT = "magenta"
_POLL_MS = 50


def _virtual_screen():
    try:
        import ctypes
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))
    except Exception:
        return (0, 0, 1920, 1080)


class TkOverlay:
    def __init__(self, show_seconds: float = 15.0):
        self.show_seconds = show_seconds
        self.last_error = ""
        self.up = False
        self.last_drawn: list = []
        self._cmd: "queue.Queue" = queue.Queue()
        self._ready = threading.Event()
        self._win = None
        self._canvas = None
        self._vx, self._vy = 0, 0
        self._hide_after = None
        self._window_dead = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10.0):
            raise RuntimeError("overlay thread did not start")
        if self.last_error:
            raise RuntimeError(self.last_error)

    # ---- caller side (any thread) ----

    def show(self, targets) -> bool:
        try:
            self._cmd.put(("show", list(targets)))
            return True
        except Exception as e:
            self.last_error = str(e)[:200]
            return False

    def clear(self) -> None:
        try:
            self._cmd.put(("clear",))
        except Exception as e:
            self.last_error = str(e)[:200]

    def shutdown(self) -> None:
        try:
            self._cmd.put(("stop",))
        except Exception:
            pass

    def inspect(self) -> dict:
        """Live canvas/window diagnostics from the overlay thread."""
        try:
            import queue as _q
            box: list = []
            self._cmd.put(("__inspect__", box))
            import time as _t
            t0 = _t.time()
            while not box and _t.time() - t0 < 5.0:
                _t.sleep(0.05)
            return dict(box[0]) if box else {}
        except Exception as e:
            return {"error": str(e)[:200]}

    # ---- overlay thread ----

    def _run(self):
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
            import tkinter as tk
            # Hidden master owns the mainloop; the visible overlay is
            # a lazily created Toplevel (no window until first show).
            master = tk.Tk()
            master.withdraw()
            try:
                master.geometry("1x1+0+0")
            except Exception:
                pass
            self._tk = tk
            self._master = master
            self._ready.set()
            master.after(_POLL_MS, self._pump)
            master.mainloop()
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning(f"overlay thread failed: {e}")
            self._ready.set()

    @staticmethod
    def _own_tk_windows() -> list:
        """All visible 'tk'-titled top-level windows of THIS process.

        In-thread winfo_id reads can disagree with what the window
        manager hit-tests (seen live), so the ground truth is external
        enumeration of our own pid — the exact check an outside
        inspector would run.
        """
        found = []
        try:
            import win32gui
            import win32process
            import win32con
            me = win32process.GetCurrentProcessId()

            def _cb(hwnd, _):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    if win32gui.GetWindowText(hwnd) != "tk":
                        return
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid != me:
                        return
                    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                    r = win32gui.GetWindowRect(hwnd)
                    found.append({
                        "hwnd": hwnd, "rect": r,
                        "transparent": bool(
                            ex & win32con.WS_EX_TRANSPARENT),
                        "noactivate": bool(
                            ex & win32con.WS_EX_NOACTIVATE),
                    })
                except Exception:
                    pass

            win32gui.EnumWindows(_cb, None)
        except Exception as e:
            log.warning(f"self window audit failed: {e}")
        return found

    @classmethod
    def _audit_self(cls, heal: bool = True) -> bool:
        """True when no own window can eat clicks. Heals by force."""
        try:
            import win32gui
            import win32con
            bad = False
            for w in cls._own_tk_windows():
                try:
                    area = max(0, w["rect"][2] - w["rect"][0]) * max(
                        0, w["rect"][3] - w["rect"][1])
                except Exception:
                    area = 0
                if area < 50 * 50:
                    continue  # specks cannot eat meaningful clicks
                if w["transparent"] and w["noactivate"]:
                    continue
                bad = True
                if not heal:
                    return False
                try:
                    ex = win32gui.GetWindowLong(
                        w["hwnd"], win32con.GWL_EXSTYLE)
                    win32gui.SetWindowLong(
                        w["hwnd"], win32con.GWL_EXSTYLE,
                        ex | win32con.WS_EX_LAYERED
                        | win32con.WS_EX_TRANSPARENT
                        | win32con.WS_EX_NOACTIVATE
                        | win32con.WS_EX_TOOLWINDOW)
                    back = win32gui.GetWindowLong(
                        w["hwnd"], win32con.GWL_EXSTYLE)
                    if not (back & win32con.WS_EX_TRANSPARENT):
                        return False
                except Exception:
                    return False
            return not bad or heal
        except Exception as e:
            log.warning(f"self audit failed: {e}")
            return False

    @staticmethod
    def _assert_click_through(hwnd, win=None) -> bool:
        """Verify click-through styles; re-apply once if wiped.

        NOTE: WS_EX_TOPMOST is deliberately NOT in the style mask.
        Topmost is a positioning state (SetWindowPos; Tk's -topmost
        already sets it) and never appears in GWL_EXSTYLE — requiring
        it there rejects every window forever (found live).
        """
        try:
            import win32gui
            import win32con
            need = (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
                    | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if (ex & need) != need:
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                       ex | need)
                ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if (ex & need) != need:
                return False
            if win is not None:
                try:
                    if not win.attributes("-topmost"):
                        return False
                except Exception:
                    pass
            return True
        except Exception as e:
            log.warning(f"click-through assert failed: {e}")
            return False

    def _ensure_window(self) -> bool:
        """Create (once) and verify a click-through window. False = dead."""
        if self._window_dead:
            return False
        if self._win is not None:
            try:
                if self._win.winfo_exists():
                    hwnd = self._win.winfo_id()
                    if self._assert_click_through(hwnd, self._win):
                        self.up = True
                        return True
                    raise RuntimeError(
                        "click-through lost on live window")
            except RuntimeError:
                raise
            except Exception:
                pass
            self._kill_window()
            self._window_dead = True
            self.up = False
            return False
        win = None
        try:
            vx, vy, vw, vh = _virtual_screen()
            win = self._tk.Toplevel(self._master)
            win.overrideredirect(True)
            win.geometry(f"{vw}x{vh}+{vx}+{vy}")
            win.configure(bg=_TRANSPARENT)
            win.attributes("-topmost", True)
            win.attributes("-transparentcolor", _TRANSPARENT)
            try:
                win.attributes("-disabled", True)
            except Exception:
                pass
            canvas = self._tk.Canvas(
                win, width=vw, height=vh, bg=_TRANSPARENT,
                highlightthickness=0, bd=0)
            canvas.place(x=0, y=0, width=vw, height=vh)
            # Map FIRST: Tk applies its own styles at map time and would
            # wipe flags set on the unmapped window (seen live).
            import win32gui
            import win32con
            hwnd = win.winfo_id()
            try:
                import ctypes
                ctypes.windll.user32.ShowWindow(
                    hwnd, win32con.SW_SHOWNOACTIVATE)
            except Exception:
                pass
            try:
                win.update_idletasks()
                win.update()
            except Exception:
                pass
            hwnd = win.winfo_id()
            # Settle loop: Tk applies some of its own styles lazily at
            # map/idle time and can overwrite ours (observed live as a
            # verified-then-solid window). Our flags go on LAST, and we
            # only accept the window when the bits survive a full idle
            # flush. Never accept on a single write.
            settled = False
            try:
                import ctypes as _ct
                import win32gui as _wg
                import win32con as _wc
                for _ in range(5):
                    try:
                        win.update_idletasks()
                        win.update()
                    except Exception:
                        pass
                    hwnd = win.winfo_id()
                    if not self._assert_click_through(hwnd, win):
                        continue
                    try:
                        win.update_idletasks()
                        win.update()
                    except Exception:
                        pass
                    hwnd = win.winfo_id()
                    if self._assert_click_through(hwnd, win):
                        settled = True
                        break
            except Exception as e:
                log.warning(f"overlay settle failed: {e}")
            if not settled:
                raise RuntimeError(
                    "click-through flags would not survive idle flush")
            # Ground truth is external enumeration of our own pid (an
            # in-thread winfo_id read once disagreed with what the
            # window manager hit-tests). Heal-or-die here.
            if not TkOverlay._audit_self(heal=True):
                raise RuntimeError(
                    "own visible window cannot be made click-through")
            self._win = win
            self._canvas = canvas
            self._vx, self._vy = vx, vy
            self.up = True
            return True
        except Exception as e:
            self.last_error = f"overlay window rejected: {e}"[:200]
            log.warning(self.last_error)
            try:
                if win is not None:
                    win.destroy()
            except Exception:
                pass
            self._win, self._canvas = None, None
            self._window_dead = True
            self.up = False
            return False

    def _kill_window(self) -> None:
        try:
            if self._win is not None:
                try:
                    self._win.destroy()
                except Exception:
                    pass
        finally:
            self._win, self._canvas = None, None
            self.up = False

    def _pump(self):
        try:
            # Runtime guard: re-audit our own visible windows every
            # tick (external ground truth, heals by force); kill the
            # window rather than let anything sit able to eat clicks.
            try:
                if not TkOverlay._audit_self(heal=True):
                    log.warning("overlay self-audit failed; killing window")
                    self._kill_window()
                    self._window_dead = True
            except Exception as e:
                log.warning(f"overlay guard failed: {e}")
            if self._win is not None:
                try:
                    if self._win.winfo_exists():
                        _hwnd = self._win.winfo_id()
                        import os as _os
                        if _os.environ.get("DUDE_OVERLAY_DEBUG"):
                            import win32gui as _wg
                            import win32con as _wc
                            _ex = _wg.GetWindowLong(
                                _hwnd, _wc.GWL_EXSTYLE)
                            log.warning(
                                "overlay tick hwnd=%s ex=0x%08x",
                                _hwnd, _ex)
                        if not self._assert_click_through(
                                _hwnd, self._win):
                            log.warning(
                                "overlay flags wiped at runtime; "
                                "killing window: %s", self.last_error)
                            self._kill_window()
                            self._window_dead = True
                    else:
                        self._win, self._canvas = None, None
                        self.up = False
                except Exception as e:
                    log.warning(f"overlay guard failed: {e}")
            while True:
                try:
                    cmd = self._cmd.get_nowait()
                except queue.Empty:
                    break
                kind = cmd[0]
                if kind == "show":
                    if self._ensure_window():
                        self._draw(cmd[1])
                    else:
                        self.last_error = self.last_error or \
                            "overlay unavailable"
                elif kind == "clear":
                    self._clear()
                elif kind == "__inspect__":
                    try:
                        c = self._canvas
                        cmd[1].append({
                            "win_exists": bool(
                                self._win is not None
                                and self._win.winfo_exists()),
                            "canvas_items": list(c.find_all()) if c is not None else None,
                            "canvas_size": (c.winfo_width(), c.winfo_height(),
                                            c.winfo_ismapped(),
                                            c.winfo_viewable())
                            if c is not None else None,
                            "win_size": (self._win.winfo_width(),
                                         self._win.winfo_height())
                            if self._win is not None else None,
                            "drawn": list(self.last_drawn),
                        })
                    except Exception as e:
                        cmd[1].append({"error": str(e)[:200]})
                elif kind == "stop":
                    try:
                        self._clear()
                        if self._win is not None:
                            self._win.destroy()
                    except Exception:
                        pass
                    self._win, self._canvas = None, None
                    self.up = False
                    try:
                        self._master.quit()
                    except Exception:
                        pass
                    return
        except Exception as e:
            log.warning(f"overlay pump failed: {e}")
        try:
            self._master.after(_POLL_MS, self._pump)
        except Exception:
            pass

    def _draw(self, targets):
        from .spatial_workspace import _rect_ok
        try:
            c = self._canvas
            if c is None:
                return
            c.delete("all")
            drawn = []
            for t in targets:
                rect = getattr(t, "rect", ())
                if not _rect_ok(rect):
                    continue
                x, y, w, h = (int(rect[0]) - self._vx, int(rect[1]) - self._vy,
                              int(rect[2]), int(rect[3]))
                c.create_rectangle(x - 2, y - 2, x + w + 2, y + h + 2,
                                   outline="#003338", width=7)
                c.create_rectangle(x - 2, y - 2, x + w + 2, y + h + 2,
                                   outline="#00E5FF", width=3)
                label = str(getattr(t, "label", "")
                            or getattr(t, "name", ""))[:40]
                drawn.append({"rect": tuple(rect), "label": label})
                if label:
                    lx = max(0, min(x, 9999))
                    ly = y - 30 if y - 30 >= 0 else y + h + 4
                    try:
                        fid = c.create_text(lx + 4, ly + 4, text=label,
                                            anchor="nw", fill="#D9FBFF",
                                            font=("Segoe UI", 11, "bold"))
                        bb = c.bbox(fid)
                        c.create_rectangle(bb[0] - 4, bb[1] - 2, bb[2] + 4,
                                           bb[3] + 2, fill="#0B2B30",
                                           outline="#00E5FF", width=1)
                        c.tag_raise(fid)
                    except Exception:
                        pass
            try:
                if self._hide_after is not None:
                    self._master.after_cancel(self._hide_after)
            except Exception:
                pass
            try:
                ms = int(float(self.show_seconds) * 1000)
                if ms > 0:
                    self._hide_after = self._master.after(ms, self._clear)
            except Exception:
                pass
            self.last_drawn = drawn
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning(f"overlay draw failed: {e}")

    def _clear(self):
        try:
            if self._canvas is not None:
                self._canvas.delete("all")
        except Exception as e:
            self.last_error = str(e)[:200]

    @property
    def ready(self) -> bool:
        """Backend usable (does not require a window to exist yet)."""
        try:
            if self._window_dead:
                return False
            if self.last_error and not self.up:
                return False
            return self._thread.is_alive()
        except Exception:
            return False

    @property
    def last_drawn(self) -> list:
        """Items rasterized by the most recent draw (geometry proof)."""
        try:
            return list(getattr(self, "_drawn", []))
        except Exception:
            return []

    @last_drawn.setter
    def last_drawn(self, value) -> None:
        try:
            self._drawn = list(value or [])
        except Exception:
            pass
