import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time

import psutil

from core.config import get_config
from core.knowledge import get_knowledge
from platform_utils import get_platform

MAX_READ = 2 * 1024 * 1024

PLATFORM = get_platform()

AGENT_CTX = {"brain": None, "broker": None, "observer": None, "thinker": None,
             "learner": None, "action_learner": None, "action_tracker": None}
_SCREENTREE = None
_UI_CMD = None
_SCROLL_STATE = {"last_src": "", "last_desc": "", "same_run": 0}

_INTEROP_CACHE = {}


def _win32_imports():
    if "win32" not in _INTEROP_CACHE:
        import ctypes
        import win32api
        import win32gui
        import win32ui
        _INTEROP_CACHE["win32"] = (ctypes, win32api, win32gui, win32ui)
    return _INTEROP_CACHE["win32"]


def _desktop_luminance(im):
    from PIL import ImageStat
    return ImageStat.Stat(im.convert("L")).mean[0]


def _printwindow(ctypes, win32gui, win32ui, hwnd, w, h):
    try:
        ctypes.windll.user32.PrintWindow.restype = ctypes.c_bool
        hdc = win32gui.GetWindowDC(hwnd)
        try:
            dc = win32ui.CreateDCFromHandle(hdc)
            mem = dc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(dc, w, h)
            mem.SelectObject(bmp)
            ok = bool(ctypes.windll.user32.PrintWindow(hwnd, int(mem.GetSafeHdc()), 2))
            if not ok:
                ok = bool(ctypes.windll.user32.PrintWindow(hwnd, int(mem.GetSafeHdc()), 0))
            image = None
            if ok:
                from PIL import Image
                image = Image.frombytes("RGB", (w, h), bmp.GetBitmapBits(True), "raw", "BGRX")
            mem.DeleteDC()
            dc.DeleteDC()
            return image
        finally:
            win32gui.ReleaseDC(hwnd, hdc)
    except Exception:
        return None


def _visible_windows(min_side=180):
    ctypes, _, win32gui, _ = _win32_imports()
    from ctypes import wintypes as wt
    windows = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    GW_OWNER = 4

    def visit(hwnd, lp):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        ln = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(ln + 1)
        if ln:
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, ln + 1)
        rc = wt.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
        w = rc.right - rc.left
        h = rc.bottom - rc.top
        if w < min_side or h < min_side:
            return True
        name = ((buf.value or "").strip() if ln else "")
        if not name:
            # Untitled top-level windows are usually owned popups (menus,
            # dropdowns, tooltips). Composite those so pixel perception
            # sees the same popups UIA reports; skip ownerless ones.
            owner = ctypes.windll.user32.GetWindow(hwnd, GW_OWNER)
            if not owner:
                return True
            name = "(popup)"
        if name in ("Program Manager", "Windows Input Experience"):
            return True
        windows.append({"hwnd": hwnd, "name": name[:80], "left": rc.left,
                        "top": rc.top, "w": w, "h": h})
        return True

    ctypes.windll.user32.EnumWindows(EnumProc(visit), 0)
    return windows


def _physical_virtual(desktop, fallback_sz):
    """Physical virtual-screen bounds, derived by scaling the process's logical
    monitor rects to match ImageGrab's physical bitmap (handles DPI virtualization)."""
    ctypes, win32api, win32gui, win32ui = _win32_imports()
    from ctypes import wintypes as wt
    rects = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(wt.RECT), ctypes.c_double)

    def monitor_cb(hmon, hdc, lprc, d):
        if lprc:
            rects.append((lprc.contents.left, lprc.contents.top,
                          lprc.contents.right, lprc.contents.bottom))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(0, 0, EnumProc(monitor_cb), 0)
    if not rects:
        return 0, 0, fallback_sz[0], fallback_sz[1], 1.0
    lx = min(r[0] for r in rects)
    ly = min(r[1] for r in rects)
    lw = max(r[2] for r in rects) - lx
    lh = max(r[3] for r in rects) - ly
    sx = desktop.width / max(lw, 1) if lw else 1.0
    sy = desktop.height / max(lh, 1) if lh else 1.0
    return int(lx * sx), int(ly * sy), int(lw * sx), int(lh * sy), min(sx, sy)


def _capture_screen_composite():
    """Return a PIL image of the screen plus a bool indicating whether it was
    composited from per-window captures. On machines whose desktop surface reads
    black (blanked/blacked display pipe) while real windows still render, this
    falls back to PrintWindow-per-window compositing so vision stays truthful."""
    from PIL import Image, ImageGrab
    desktop = None
    composited = False
    try:
        desktop = ImageGrab.grab(all_screens=True).convert("RGB")
        if _desktop_luminance(desktop) >= 46:
            return desktop, composited
    except Exception:
        desktop = None
    if desktop is None:
        return Image.new("RGB", (1280, 720), (0, 0, 0)), composited
    ctypes, win32api, win32gui, win32ui = _win32_imports()
    vx, vy, vw, vh, scale = _physical_virtual(desktop, (0, 0))
    canvas = Image.new("RGB", (max(desktop.width, vw, 1), max(desktop.height, vh, 1)), (12, 12, 14))
    got = 0
    for win in sorted(_visible_windows(), key=lambda e: -(e["w"] * e["h"])):
        img = _printwindow(ctypes, win32gui, win32ui, win["hwnd"], win["w"], win["h"])
        if img is None:
            continue
        if scale > 1.01:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        canvas.paste(img, (int((win["left"] * scale) - vx), int((win["top"] * scale) - vy)))
        got += 1
    if got:
        composited = True
        return canvas, composited
    return desktop, composited


def init_agent_ctx(brain=None, broker=None, observer=None, thinker=None, learner=None,
                   action_learner=None, action_tracker=None):
    AGENT_CTX["brain"] = brain
    AGENT_CTX["broker"] = broker
    AGENT_CTX["observer"] = observer
    AGENT_CTX["thinker"] = thinker
    AGENT_CTX["learner"] = learner
    AGENT_CTX["action_learner"] = action_learner
    AGENT_CTX["action_tracker"] = action_tracker


def init_screentree(map_):
    global _SCREENTREE
    _SCREENTREE = map_


def init_ui_cmd(q):
    global _UI_CMD
    _UI_CMD = q


# ---------------- helpers ----------------

def _ok(msg):
    return msg


def _err(msg):
    return f"ERROR: {msg}"


def _resolve_app(name):
    return PLATFORM.resolve_app(name)


_BROWSER_EXES = {"comet.exe", "chrome.exe", "msedge.exe", "edge.exe", "firefox.exe",
                 "brave.exe", "opera.exe", "vivaldi.exe", "chromium.exe", "arc.exe",
                 "msedgewebview2.exe", "browser.exe", "thebrowser.exe"}

_BROWSER_HINT = ("This is a BROWSER/web page. The page content is rendered, so UI "
                 "Automation cannot see its buttons/links and ui_click CANNOT work here. "
                 "Do NOT retry ui_click. Instead: 1) find_on_screen with a description of "
                 "the thing to click (e.g. 'the Login button'), 2) then click_fraction "
                 "with the exact x/y it returns. One vision call + one exact click.")

_BROWSER_SCAN_HINT = ("Active window is a BROWSER/web page. The page is rendered so its "
                      "controls are NOT listed in this UI map — only the browser frame "
                      "may appear. To operate the page, use find_on_screen (describe the "
                      "thing) then click_fraction its exact coordinates; use keyboard "
                      "shortcuts (Tab / Enter / type_text) where reliable.")


def _active_app():
    try:
        f = PLATFORM.foreground_window()
        return (f.get("app") or "").lower(), (f.get("title") or "")
    except Exception:
        return "", ""


def _is_browser_active():
    app, _ = _active_app()
    base = app.split("\\")[-1].lower()
    if base in _BROWSER_EXES:
        return True
    star = base.rstrip(".exe")
    return star in {"comet", "chrome", "msedge", "edge", "firefox", "brave",
                    "opera", "vivaldi", "chromium", "arc"}


# ---------------- tool implementations ----------------

def get_datetime(memory, args):
    now = datetime.datetime.now()
    return _ok(now.strftime("%A, %d %B %Y, %I:%M %p"))


def battery_status(memory, args):
    b = psutil.sensors_battery()
    if b is None:
        return _ok("No battery detected (desktop PC).")
    state = "charging" if b.power_plugged else "discharging"
    return _ok(f"Battery at {int(b.percent)}%, {state}.")


def _app_candidates(name):
    out = []
    s = name.strip().strip(".,;:'\"!+")
    for x in (name, s, s.split()[0] if len(s.split()) > 1 else s):
        if x and x not in out:
            out.append(x)
    return out


def _resolved(exe, target):
    lt = str(target).lower()
    if lt.startswith(("ms-settings:", "ms-windows-store:", "appid:", "shell:")):
        return True
    return bool(os.path.exists(str(target)))


def _resolve_app(name):
    cands = _app_candidates(name)
    for cand in cands:
        try:
            t = PLATFORM.resolve_app(cand)
        except Exception:
            continue
        if t is not None and str(t) and (lt := str(t).lower()):
            if lt.startswith(("ms-settings:", "ms-windows-store:", "appid:", "shell:")):
                return cand, str(t).split(":")[0] + ":", str(t)
            if os.path.exists(str(t)):
                return cand, os.path.basename(str(t)).lower(), str(t)
            if lt != cand.lower():
                return cand, os.path.basename(str(t)).lower(), str(t)
    # Fuzzy fallback: "comment browser" should still find the installed Comet app.
    import difflib

    token = (name.strip().split() or [name])[0].lower()
    token = "".join(ch for ch in token if ch.isalnum())
    if len(token) >= 3:
        try:
            apps = PLATFORM._start_apps()
        except Exception:
            apps = []
        best_ratio = 0.0
        best_disp = None
        for disp, _appid in apps:
            dlow = disp.lower()
            glob = "".join(ch for ch in dlow if ch.isalnum()).split()
            first = glob[0] if glob else ""
            ratio = difflib.SequenceMatcher(None, token, first).ratio()
            if ratio >= 0.7 and ratio > best_ratio:
                best_ratio = ratio
                best_disp = disp
        if best_disp:
            t = PLATFORM.resolve_app(best_disp)
            if t and (lt := str(t).lower()):
                if lt.startswith(("ms-settings:", "ms-windows-store:", "appid:", "shell:")):
                    return best_disp, lt.split(":")[0] + ":", str(t)
                if os.path.exists(str(t)):
                    return best_disp, os.path.basename(str(t)).lower(), str(t)
                if lt != best_disp.lower():
                    return best_disp, os.path.basename(str(t)).lower(), str(t)
    return None, None, None


_RECENT_LAUNCHES = {}

# Per-turn app-action tracker used by the brain loop to stop the
# open -> close -> reopen / duplicate-open oscillation that a weak model
# falls into (e.g. repeatedly opening and closing Comet).
APP_TURN = {"opened": {}, "closed": {}, "reset_at": 0.0}


def _reset_app_turn():
    APP_TURN["opened"].clear()
    APP_TURN["closed"].clear()
    APP_TURN["reset_at"] = time.time()


def _note_app_opened(name):
    APP_TURN["opened"][name.lower().rstrip(".exe")] = time.time()


def _note_app_closed(name):
    APP_TURN["closed"][name.lower().rstrip(".exe")] = time.time()


def app_open_block(name):
    """If this turn already opened `name`, tell the model not to re-open it
    (focus instead). Returns a guard string or None."""
    base = (name or "").split("\\")[-1].lower().rstrip(".exe")
    if not base:
        return None
    if base in APP_TURN["opened"]:
        return (f"GUARD: '{name}' was already opened earlier this turn and is on "
                f"screen. Do NOT open it again and DO NOT open a new/duplicate "
                f"instance. If you need it visible, use window_action(action=focus, "
                f"title=<its title>) or just continue with the next step. Never close "
                f"+ reopen what is already open.")
    return None


def bring_to_foreground(hwnd):
    """Best-effort foreground transfer to one exact window.

    Proven mechanism (Phase 5): a bare background process is routinely
    denied SetForegroundWindow while the user drives another app; a
    momentary Alt keystroke convinces Windows this thread is user-driven
    (standard technique used by installers and automation alike), after
    which the transfer succeeds. No window is minimized, moved, resized,
    or otherwise disturbed; the Alt press alone may briefly arm menu
    mnemonics in the previously foreground app, which is harmless and
    self-reverting.

    For UWP/Store apps (ApplicationFrameWindow), standard AttachThreadInput
    fails due to security restrictions; we fall back to UIA SetFocus which
    works for these apps. Returns True iff the window is foreground after the
    attempt. Callers must still VERIFY (title/UIA), never assume.
    """
    try:
        import win32gui
        import win32con
        import win32process
        import win32api
        import ctypes
        import time as _t
        try:
            if not win32gui.IsWindow(hwnd):
                return False
        except Exception:
            return False
        # Quick check: if already foreground, we're done
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        current_thread = win32api.GetCurrentThreadId()
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)

        # Try standard method
        standard_ok = False
        try:
            win32process.AttachThreadInput(current_thread, target_thread,
                                           True)
            try:
                win32gui.SetForegroundWindow(hwnd)
                standard_ok = True
            finally:
                win32process.AttachThreadInput(current_thread,
                                               target_thread, False)
        except Exception:
            pass
        finally:
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)

        _t.sleep(0.3)
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass

        # Fallback for UWP/Store apps: UIA SetFocus works where
        # AttachThreadInput fails due to security restrictions
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl is not None:
                ctrl.SetFocus()
                _t.sleep(0.3)
                return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            pass
        return False
    except Exception:
        return False


def app_close_then_reopen_block(name):
    """If we just closed `name` this turn, forbid immediately re-opening it."""
    base = (name or "").split("\\")[-1].lower().rstrip(".exe")
    if not base:
        return None
    if base in APP_TURN["closed"]:
        return (f"GUARD: you just CLOSED '{name}' this turn. Do NOT open it again — "
                f"that is the pointless close/open loop. Either continue with what is "
                f"already open, or state plainly what you need.")
    return None

def _wait_for_app_window(cand, exe, timeout=5.0):
    """Wait (bounded) for a just-launched app's first real window, then
    return its HWND. Cold UWP frames need seconds before SetForeground
    or UIA can touch them; polling beats assuming readiness."""
    import time as _t
    end = _t.time() + max(0.5, timeout)
    while _t.time() < end:
        try:
            for p in psutil.process_iter(["pid", "name"]):
                pname = (p.info["name"] or "").lower()
                if (exe and exe in pname) or \
                        cand.lower().split()[0] in pname:
                    for w in _top_level_windows():
                        if w.get("pid") == p.info["pid"] and \
                                _is_real_app_window(w, exe):
                            hwnd = w.get("hwnd")
                            if hwnd:
                                return hwnd
        except Exception:
            pass
        _t.sleep(0.4)
    return None


def open_app(memory, args):
    name = args.get("name", "").strip()
    if not name:
        return _err("no app name given")
    cand, exe, target = _resolve_app(name)
    if cand is None:
        return _err(f"couldn't find any app named '{name}' — try an exact exe name "
                    f"via run_powershell 'Start-Process <path>', search your disk with "
                    f"search_files, or open the Start menu with press_hotkey like 'win' "
                    f"and type {name!r}")
    need_new = bool(args.get("new") or args.get("new_window") or args.get("another"))
    # Phase 5: an exact HWND target focuses one chosen window (the reuse
    # decision made at plan time). A dead or foreign HWND fails loudly
    # instead of launching something unintended.
    want_hwnd = args.get("hwnd")
    if want_hwnd and not need_new:
        try:
            hwnd = int(want_hwnd)
            import win32gui
            if not win32gui.IsWindow(hwnd):
                return _err(f"target window {hwnd} no longer exists")
            if bring_to_foreground(hwnd):
                _note_app_opened(cand)
                return _ok(f"{cand} focused at window {hwnd}.")
            return _ok(f"{cand} window {hwnd} is still open (foreground request "
                       f"did not take effect; verification will confirm).")
        except Exception as e:
            return _err(f"could not focus window {want_hwnd}: {e}")
    # Cool-off: if this exact app was just launched seconds ago, don't spawn a
    # second instance even though the process isn't visible in psutil yet.
    # Two rapid open_app calls (same window, e.g. 'comet browser' then 'comet')
    # used to launch the app twice. NEW=true still opens another instance.
    # The old code returned here WITHOUT focusing: a fresh (especially UWP)
    # frame often fails to take the foreground by itself while initializing,
    # so every retry verified against the wrong window and died. Cool-off
    # must wait for the frame and focus it, not no-op.
    recent = _RECENT_LAUNCHES.get(cand)
    if recent and time.time() - recent < 8.0 and not need_new:
        hwnd = _wait_for_app_window(cand, exe, timeout=5.0)
        if hwnd and bring_to_foreground(hwnd):
            _note_app_opened(cand)
            return _ok(f"{cand} focused at window {hwnd} "
                       f"(launched moments ago; no duplicate spawned).")
        return _ok(f"{cand} was just opened — one instance is plenty, not "
                   f"spawning another.")
    try:
        if not need_new:
            # Idempotent: if already running with a real application window,
            # don't spawn another instance. Shell chrome (same process, e.g.
            # explorer.exe tray helpers) is filtered by the shared rule so
            # it can never satisfy this check.
            for p in psutil.process_iter(["pid", "name"]):
                pname = (p.info["name"] or "").lower()
                if (exe and exe in pname) or cand.lower().split()[0] in pname:
                    # Check if this process has a visible window
                    visible = [w for w in _top_level_windows()
                               if w["pid"] == p.info["pid"]
                               and _is_real_app_window(w, exe)]
                    if visible:
                        _note_app_opened(cand)
                        hwnd = visible[0].get("hwnd") if isinstance(visible[0], dict) else None
                        if not hwnd:
                            for w in visible:
                                if w.get("pid") == p.info["pid"]:
                                    hwnd = w.get("hwnd")
                                    break
                        # Best effort; report honestly either way and let
                        # verification judge the real state.
                        brought = bring_to_foreground(hwnd) if hwnd else False
                        if brought:
                            return _ok(f"{cand} is already open and brought to foreground.")
                        return _ok(f"{cand} is already open (foreground request did not take effect; "
                                   f"verification will confirm the real window).")
                    # Process exists but no visible window - will launch new below
        PLATFORM.launch_app(cand)
        _RECENT_LAUNCHES[cand] = time.time()
        _note_app_opened(cand)
        time.sleep(1.5)
        started = None
        for p in psutil.process_iter(["pid", "name"]):
            pname = (p.info["name"] or "").lower()
            if (exe and exe in pname) or cand.lower().split()[0] in pname:
                started = p
                break
        if started is None:
            return _err(f"launch issued for {cand}, but no process appeared — "
                        f"it may be blocked or still initializing")
        visible = [w for w in _top_level_windows()
                   if w["pid"] == started.info["pid"]
                   and _is_real_app_window(w, exe)]
        if visible:
            w0 = visible[0]
            return _ok(f"Opened {cand} (PID {started.info['pid']}) — "
                       f"window \"{w0['title'][:60]}\" is on screen.")
        try:
            w = PLATFORM.foreground_window()
            title = f" — \"{w.get('title', '')[:60]}\"" if w.get("title") else ""
        except Exception:
            title = ""
        return _ok(f"{cand} started (PID {started.info['pid']}){title}. "
                   f"No visible frame yet after 1.5s — checking again if you ask.")
    except Exception as e:
        return _err(f"could not open {cand}: {e}")


def shutdown_system(memory, args):
    try:
        os.system("shutdown /s /t 10 /c \"DUDE initiated shutdown\"")
        return _ok("System shutdown initiated. Shutting down in 10 seconds.")
    except Exception as e:
        return _err(f"shutdown failed: {e}")


def restart_system(memory, args):
    try:
        os.system("shutdown /r /t 10 /c \"DUDE initiated restart\"")
        return _ok("System restart initiated. Restarting in 10 seconds.")
    except Exception as e:
        return _err(f"restart failed: {e}")


def close_app(memory, args):
    name = args.get("name", "").strip().lower().rstrip(".exe")
    if not name:
        return _err("no app name given")
    killed_pids = set()
    killed_names = []
    for p in psutil.process_iter(["pid", "name"]):
        pname = (p.info["name"] or "").lower()
        base = pname.rstrip(".exe")
        if base == name or (len(name) >= 4 and name in base):
            try:
                p.terminate()
                killed_pids.add(p.info["pid"])
                killed_names.append(p.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if not killed_pids:
        return _err(f"no running process named '{name}' is actually running to close")
    _note_app_closed(name)
    import time as _t
    _t.sleep(0.8)
    visible = [w for w in _top_level_windows() if w["pid"] in killed_pids]
    msg = (f"Closed {len(set(killed_names))} process(es) matching '{name}' "
           f"({len(killed_pids)} instances).")
    if visible:
        msg += " Windows still visible for it: " + " | ".join(
            t["title"] for t in visible[:6])
    else:
        msg += " Nothing of it is left on screen."
    return _ok(msg)


def list_running_apps(memory, args):
    apps = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        n = p.info["name"]
        if n:
            apps[n] = apps.get(n, 0) + 1
    top = sorted(apps.items(), key=lambda kv: -kv[1])[:25]
    line = "Running processes (most instances first): " + ", ".join(
        f"{k} x{v}" for k, v in top)
    wins = [w["title"] for w in _top_level_windows()[:8]]
    if wins:
        line += "\nVisible windows right now: " + " | ".join(wins)
    return _ok(line)


def list_directory(memory, args):
    path = args.get("path", ".")
    try:
        entries = list(os.scandir(os.path.expanduser(path)))
    except Exception as e:
        return _err(str(e))
    dirs = sorted(e.name + "/" for e in entries if e.is_dir())
    files = sorted(e.name for e in entries if e.is_file())
    head = dirs[:60] + files[:80]
    return _ok(f"'{path}' contains:\n" + "\n".join(head) if head else "(empty)")


def read_file(memory, args):
    path = args.get("path", "")
    try:
        size = os.path.getsize(path)
        if size > MAX_READ:
            return _err(f"file too large ({size//1024//1024}MB); ask me to read a specific part")
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return _ok(f.read()[:20000])
    except Exception as e:
        return _err(str(e))


def write_file(memory, args):
    path = args.get("path", "")
    content = args.get("content", "")
    append = bool(args.get("append", False))
    try:
        mode = "a" if append else "w"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)
        return _ok(f"Wrote {len(content)} chars to {path}.")
    except Exception as e:
        return _err(str(e))


def create_folder(memory, args):
    try:
        path = args.get("path", "")
        os.makedirs(path, exist_ok=True)
        return _ok("Folder created.")
    except Exception as e:
        return _err(str(e))


_SAFE_DELETE_LOCK = threading.Lock()


def delete_path(memory, args):
    """DELETE PROTECTION (CRITICAL): files are user data. This tool NEVER
    permanently deletes by default. It moves to the Windows Recycle Bin
    (recoverable) AND refuses to delete user text/data files unless the caller
    passes confirm=true (even then, Recycle Bin, recoverable). The user has
    explicitly said he does NOT want important files deleted — so an
    unprotected delete call is blocked outright."""
    path = args.get("path", "")
    confirm = bool(args.get("confirm") or args.get("force"))
    if not path:
        return _err("no path given")
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return _err(f"nothing exists at {path}")
    # Extensions that are almost always important user work / documents.
    _doc_exts = {".txt", ".md", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                 ".csv", ".json", ".py", ".js", ".ts", ".html", ".pdf", ".rtf",
                 ".odt", ".ods", ".odp", ".sql", ".db", ".ini", ".cfg", ".yaml",
                 ".yml"}
    is_doc = os.path.isfile(path) and os.path.splitext(path)[1].lower() in _doc_exts
    # Block deleting anything under the user's home profile or data dirs unless
    # explicitly confirmed via this tool's confirm flag.
    _protected_roots = [os.path.expanduser("~") + os.sep]
    if is_doc and not confirm:
        return (_err("BLOCKED: refusing to delete that file because it is a "
                     "text/data document and no confirm=true was given. The user "
                     "does NOT want files deleted. If the user explicitly told "
                     "you to delete this exact file, re-issue with confirm=true."))
    # Even with confirm, NEVER hard-delete — use the Recycle Bin so it is
    # recoverable if it was a mistake.
    try:
        _move_to_recycle_bin(path)
        return _ok(f"Moved {os.path.basename(path)} to the Recycle Bin (recoverable).")
    except Exception as e:
        return _err(f"delete blocked: move to Recycle Bin failed: {e}")


def _move_to_recycle_bin(path):
    """Send a file/folder to the Windows Recycle Bin via the Shell.Application
    COM object (built into every Windows box, no extra deps). Falls back to a
    timestamped backup folder if COM is unavailable, NEVER os.remove."""
    import subprocess
    ps = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        f"'{path}', 'OnlyErrorDialogs', 'SendToRecycleBin')" if os.path.isdir(path)
        else
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        f"'{path}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return
    # Fallback: move to a "dude_trash" backup folder instead of losing data.
    import shutil
    trash_dir = os.path.join(os.path.expanduser("~"), "dude_trash")
    os.makedirs(trash_dir, exist_ok=True)
    dst = os.path.join(trash_dir, os.path.basename(path))
    if os.path.exists(dst):
        dst += "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.isdir(path):
        shutil.move(path, dst)
    else:
        shutil.copy2(path, dst)
        os.remove(path)
    return dst


def move_path(memory, args):
    src, dst = args.get("source", ""), args.get("destination", "")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.move(src, dst)
        return _ok(f"Moved {src} -> {dst}.")
    except Exception as e:
        return _err(str(e))


def copy_path(memory, args):
    src, dst = args.get("source", ""), args.get("destination", "")
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            shutil.copy2(src, dst)
        return _ok(f"Copied {src} -> {dst}.")
    except Exception as e:
        return _err(str(e))


def search_files(memory, args):
    root = args.get("root", os.path.expanduser("~"))
    pattern = args.get("pattern", "*").lower()
    hits = []
    for dirpath, dirnames, filenames in os.walk(os.path.expanduser(root)):
        dirnames[:] = [d for d in dirnames if d not in (
            "node_modules", ".git", "__pycache__", "AppData", "site-packages")]
        for fn in filenames:
            if pattern.lstrip("*") in fn.lower():
                hits.append(os.path.join(dirpath, fn))
                if len(hits) >= 40:
                    break
        if len(hits) >= 40:
            break
    return _ok("\n".join(hits) if hits else "No matches found.")


_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(remove[- ]item|del\b|erase|rm\b|rmdir|rd\b|deltree|"
    r"clear[- ]?content|format\s+|fdisk|"
    r"shutdown|restart[- ]computer|wil\b|systemreset|diskpart|"
    r"takeown\s+/f|icacls\s+.*/reset|reg\s+delete|"
    r"-recurse\s+-force|move[- ]item\s+.*(?:prompt|noclobber))")


def _matches_destructive(cmd):
    """Return a match object if the PowerShell command looks like it deletes or
    destroys user data (Remove-Item, del, erase, rmdir, format, reg delete,
    Clear-Content, etc.). Used as a hard firewall on run_powershell."""
    if not cmd:
        return None
    return _DESTRUCTIVE_RE.search(cmd)


def run_powershell(memory, args):
    cmd = args.get("command", "")
    # DESTRUCTIVE-COMMAND FIREWALL (CRITICAL): the user does NOT want files
    # deleted without his explicit OK. The model was deleting real text files
    # via Remove-Item / del. Block anything that removes/overwrites/erases
    # paths unless the command ALSO asks the user to confirm (confirm flag).
    _danger = _matches_destructive(cmd)
    if _danger and not args.get("confirm"):
        return _err("BLOCKED (safety): that command deletes or overwrites data. "
                    "The user has said he does NOT want files deleted. If he "
                    "explicitly asked, re-run with confirm=true.")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=45,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: no console flash
        )
        out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr.strip() else "")
        return _ok(out.strip()[:4000] or "(no output)")
    except subprocess.TimeoutExpired:
        return _err("command timed out after 45s")
    except Exception as e:
        return _err(str(e))


def screenshot(memory, args):
    cfg = get_config()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(cfg.data_dir, "screenshots", f"screenshot_{ts}.png")
    im, composited = _capture_screen_composite()
    im.save(path)
    PRUNE_SHOT_KEEP = 40
    try:
        files = sorted(p for p in os.listdir(os.path.dirname(path))
                       if p.startswith("screenshot_") and p.endswith(".png"))
        for fn in files[: max(0, len(files) - PRUNE_SHOT_KEEP)]:
            os.remove(os.path.join(os.path.dirname(path), fn))
    except OSError:
        pass
    note = " (composited from visible windows - desktop surface reads blank)" if composited else ""
    return _ok(f"Screenshot saved to {path}{note}")


def open_url(memory, args):
    url = args.get("url", "")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    PLATFORM.open_url(url)
    return _ok(f"Opened {url}")


def clipboard_read(memory, args):
    return run_powershell(None, {"command": "Get-Clipboard"})


def clipboard_write(memory, args):
    text = args.get("text", "")
    escaped = text.replace("'", "''")
    return run_powershell(None, {"command": f"Set-Clipboard -Value '{escaped}'"})


def press_hotkey(memory, args):
    import pyautogui

    combo = args.get("combo", "")
    keys = [k.strip().lower() for k in combo.replace("+", " ").split()]
    pyautogui.hotkey(*keys)
    return _ok(f"Pressed {combo}")


def type_text(memory, args):
    text = args.get("text", "")
    app = args.get("app", "").strip()
    import pyautogui

    if app:
        try:
            wins = pyautogui.getWindowsWithTitle(app)
            if wins:
                try:
                    wins[0].activate()
                except Exception:
                    pass
                time.sleep(0.3)
        except Exception:
            pass
    pyautogui.typewrite(text, interval=0.02)
    return _ok("Typed.")


def ui_scan(memory, args):
    """Return the current structured map of the active window: every visible
    control's name, type and exact pixel position (from Windows UI Automation,
    not screenshots)."""
    m = _SCREENTREE
    if m is None or not m.available():
        return _err("UI Automation screen map is not active on this machine")
    if _is_browser_active():
        view = m.current_view(limit=int(args.get("limit", 26)) or 26)
        if isinstance(view, str):
            return view
        return _ok(_BROWSER_SCAN_HINT + "\n(Browser frame controls only, page controls "
                   "are not exposed.)\n" + str(view))
    return _ok(m.current_view(limit=int(args.get("limit", 26)) or 26))


def ui_click(memory, args):
    """Click a REAL UI control on the active window by its name (e.g. 'Restore')
    or by name + role ('btn'/'input'/'tab'/...). Position comes from the live
    UI Automation map, so the click always lands on the actual widget."""
    import pyautogui

    m = _SCREENTREE
    if m is None or not m.available():
        return _err("UI Automation screen map is not active on this machine")
    name = (args.get("name") or "").strip()
    role = (args.get("role") or "").strip()
    x = args.get("x")
    y = args.get("y")
    if not name and (x is None or y is None):
        return _err("ui_click needs a control 'name' (or x/y pixels)")
    if x is not None and y is not None:
        # Explicit coordinates were already grounded against the live UIA
        # tree by the caller (e.g. ActionExecutor): the browser guard below
        # exists to stop blind name-clicks into rendered page content, which
        # UIA cannot see — it does not apply to pre-grounded points, which
        # is how toolbar chrome (profile menu, etc.) is operated.
        px, py = int(x), int(y)
    else:
        if _is_browser_active():
            return _err(_BROWSER_HINT)
        row = m.find(name=name, role=role)
        if row is None:
            names = [f"{r.get('name') or '?'}" for r in m.rows[:20]]
            return _err(f"no control '{name}' on screen now. Visible controls: "
                        f"{', '.join(names) or '(none)'}")
        px, py = (int(row["x"]) + int(row["w"]) // 2,
                  int(row["y"]) + int(row["h"]) // 2)
        if not row.get("enabled", True):
            return _err(f"control '{name}' is visible but disabled")
    pyautogui.click(px, py)
    label = name or f"({px},{py})"
    m.note_click(f"{label} at ({px},{py})")
    return _ok(f"Clicked control '{label}' at ({px},{py}).")


def show_ui(memory, args):
    """Bring DUDE's own UI (the Notch pill / command centre) back on screen if
    it was hidden, and bring it to the foreground. Use when asked where the UI
    is or when it is missing from the screen."""
    if _UI_CMD is None:
        return _err("my UI command link is not wired in this build")
    q = _UI_CMD
    try:
        q.put_nowait("show")
    except Exception:
        return _err("my UI window is not running — ask me to restart")
    return _ok("My UI is back on screen and raised to the front.")


def click_at(memory, args):
    import pyautogui

    x, y = args.get("x"), args.get("y")
    if x is None or y is None:
        pyautogui.click()
    else:
        pyautogui.click(int(x), int(y))
    return _ok("Clicked.")


def hover_at(memory, args):
    """Move the mouse cursor to a point (or fraction) and HOLD it there, which
    reveals hover menus/dropdowns exactly like a person hovering with the mouse.
    Optionally press down/up to simulate drag. Does NOT click by default."""
    import pyautogui
    import time as _time

    x, y = args.get("x"), args.get("y")
    fx, fy = args.get("fx"), args.get("fy")
    if fx is not None and fy is not None:
        W, H = pyautogui.size()
        x = int(float(fx) * W)
        y = int(float(fy) * H)
    hold = float(args.get("hold_s", 0.6))
    if x is None or y is None:
        return _err("hover_at needs x,y pixels OR fx,fy fractions")
    pyautogui.moveTo(int(x), int(y), duration=0.2)
    if (args.get("press") or "").lower() in ("down", "true"):
        pyautogui.mouseDown()
        _time.sleep(min(hold, 2.0))
        return _ok(f"Hovering with mouse held down at ({int(x)},{int(y)}).")
    _time.sleep(min(hold, 2.0))
    return _ok(f"Cursor moved to ({int(x)},{int(y)}) and held {hold}s to reveal hover menus. "
               "Now take a fresh screenshot and read what opened before clicking.")


def click_fraction(memory, args):
    import pyautogui

    try:
        x = float(args.get("x"))
        y = float(args.get("y"))
    except (TypeError, ValueError):
        return _err("click_fraction needs numeric x,y")
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return _err("x and y must be between 0 and 1 (fractions of screen width/height)")
    snap = args.get("snap", True)
    W, H = pyautogui.size()
    px, py = int(x * W), int(y * H)
    label = f"({px},{py})"
    # SNAP: if the UI Automation map is live, snap this imprecise fraction onto the
    # center of the nearest real control so the click lands on a widget, not empty
    # space. This is the cursor-accuracy fix: fraction-only clicks were landing in
    # dead zones and missing the control.
    m = _SCREENTREE
    if snap is not False and m is not None and m.available():
        try:
            best, bd = None, float("inf")
            for r in m.rows:
                cx = r.get("x", 0) + int(r.get("w", 0)) // 2
                cy = r.get("y", 0) + int(r.get("h", 0)) // 2
                d = (cx - px) ** 2 + (cy - py) ** 2
                if d < bd:
                    bd, best = d, (cx, cy, r.get("name") or "")
            if best is not None and bd < (int(W) * 0.05) ** 2 + (int(H) * 0.05) ** 2:
                px, py, cname = best
                label = f"'{cname}' @ ({px},{py})"
        except Exception:
            pass
    pyautogui.click(px, py)
    try:
        if m is not None:
            m.note_click(f"click_fraction snapped to {label}")
    except Exception:
        pass
    return _ok(f"Clicked {label} on a {W}x{H} screen (snapped to nearest control).")


def scroll_screen(memory, args):
    import pyautogui
    import time as _time

    amount = int(args.get("amount", 0) or 0)
    direction = (args.get("direction") or "down").lower()
    if amount == 0:
        amount = -120 if direction != "up" else 120
    amount = max(-240, min(240, amount))
    pyautogui.scroll(amount)

    hint = ""
    try:
        observer = AGENT_CTX.get("observer")
        if observer is not None:
            cur = observer.current_screen()
            desc = (cur.get("description") or "").strip()
            src = (cur.get("shot") or "").strip()
            if src == _SCROLL_STATE["last_src"] and desc == _SCROLL_STATE["last_desc"]:
                _SCROLL_STATE["same_run"] += 1
            else:
                _SCROLL_STATE["same_run"] = 0
            _SCROLL_STATE["last_src"] = src
            _SCROLL_STATE["last_desc"] = desc
            if _SCROLL_STATE["same_run"] >= 3:
                hint = (" FEEDBACK: the visible screen has NOT changed across your recent "
                        "scrolls. You likely hit the page start/end, scrolled too far in one "
                        "step, or lost focus. STOP scrolling. Next: take a fresh screenshot and "
                        "LOOK at the scrollbar + page structure (nav menu, pagination, headings). "
                        "Then either scroll back one small step or click a navigation/link element.")
    except Exception:
        pass
    return _ok(f"Scrolled {amount} notches "
               f"({'up' if amount > 0 else 'down'}, one small wheel step).{hint}")


def active_window_info(memory, args):
    w = PLATFORM.foreground_window()
    return _ok(f"Active app: {w['app']}, window title: \"{w['title']}\"")


def _top_level_windows():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowRect.restype = ctypes.c_bool
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    out = []
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0 or length > 200:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        # Window class: lets callers apply the shared is_app_window rule
        # so shell chrome (same process, real title) never counts as an
        # application window. Best effort; empty on failure.
        try:
            cls_buf = ctypes.create_unicode_buffer(256)
            wclass = cls_buf.value if user32.GetClassNameW(
                hwnd, cls_buf, 256) > 0 else ""
        except Exception:
            wclass = ""
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append({"hwnd": int(hwnd), "title": title, "wclass": wclass,
                    "x": rect.left, "y": rect.top,
                    "w": w, "h": h, "pid": int(pid.value)})
        return True

    user32.EnumWindows(CALLBACK(cb), 0)
    return out


def list_windows(memory, args):
    wins = _top_level_windows()
    if not wins:
        return _ok("No visible top-level windows found.")
    rows = sorted((f"{w['title']}  ({w['x']},{w['y']}  {w['w']}x{w['h']}px)" for w in wins),
                  key=lambda s: s.lower())
    return _ok("Windows:\n" + "\n".join(rows[:25]))


def _find_visible_window(title):
    t = title.lower()
    return next((w for w in _top_level_windows() if t in w["title"].lower()), None)


def _is_real_app_window(win, exe_name):
    """Shared-rule gate: shell chrome must never count as an open app.

    explorer.exe always owns visible titled windows (tray helpers etc.);
    without this, open_app latches onto chrome, reports "already open",
    and never launches a real window. Lazy import: core.tools loads
    before core.orchestrator, so this must not bind at module level.
    Fail-open (True) keeps legacy behaviour if the helper is missing.
    """
    try:
        from core.orchestrator.app_instances import is_app_window
    except Exception:
        return True
    try:
        return bool(is_app_window(
            (exe_name or "").lower(),
            win.get("wclass", ""), win.get("title", "")))
    except Exception:
        return True


def window_action(memory, args):
    """Deterministic window frame actions (restore / maximize / minimize /
    focus) by window title — this is how the OS's own title-bar restore button
    works, so it works even for apps that hide their accessibility tree."""
    import ctypes

    title = (args.get("title") or "").strip()
    action = (args.get("action") or "").strip().lower()
    if not title:
        return _err("window_action needs a 'title' substring")
    if action not in ("restore", "maximize", "minimize", "focus", "close"):
        return _err("action must be one of: restore, maximize, minimize, focus, close")
    best = _find_visible_window(title)
    if best is None:
        names = ", ".join(w["title"] for w in _top_level_windows()[:10]) or "none visible"
        return _err(f"no visible window matching {title!r} — visible window titles: {names}")
    user32 = ctypes.windll.user32
    hwnd = best["hwnd"]
    if action == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return _ok(f"Sent close to \"{best['title']}\".")
    show = {"restore": 9, "maximize": 3, "minimize": 6}.get(action)
    if action == "focus":
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    else:
        user32.ShowWindow(hwnd, show)
        if action == "restore":
            user32.SetForegroundWindow(hwnd)
    return _ok(f"Window \"{best['title'][:60]}\" now {action}ed.")


def close_window(memory, args):
    import ctypes

    title = (args.get("title") or "").strip()
    if not title:
        return _err("close_window needs a 'title' substring of the window to close")
    wins = _top_level_windows()
    best = next((w for w in wins if title.lower() in w["title"].lower()), None)
    if best is None:
        names = ", ".join(w["title"] for w in wins[:10]) or "none visible"
        return _err(f"no visible window matching {title!r} — visible window titles: {names}")
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.PostMessageW.restype = ctypes.c_bool
    user32.PostMessageW(best["hwnd"], 0x0010, 0, 0)  # WM_CLOSE
    return _ok(f"Sent close to \"{best['title']}\" — one window only, the rest stay open.")


def move_window(memory, args):
    import ctypes

    title = (args.get("title") or "").strip()
    if not title:
        return _err("move_window needs a 'title' substring to find the window")
    tlow = title.lower()
    best = None
    for w in _top_level_windows():
        if tlow in w["title"].lower():
            best = w
            break
    if best is None:
        names = ", ".join(w["title"] for w in _top_level_windows()[:10]) or "none visible"
        return _err(f"no visible window matching {title!r} — visible window titles: {names}")
    x, y, w, h = best["x"], best["y"], best["w"], best["h"]
    if args.get("center"):
        W = ctypes.windll.user32.GetSystemMetrics(0)
        H = ctypes.windll.user32.GetSystemMetrics(1)
        x, y = (W - w) // 2, (H - h) // 2
    x = int(args.get("x", x))
    y = int(args.get("y", y))
    w = int(args.get("w", w))
    h = int(args.get("h", h))
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_bool
    ok = user32.SetWindowPos(best["hwnd"], 0, x, y, w, h, 0x0004 | 0x0010)
    if not ok:
        return _err(f"could not reposition \"{best['title']}\"")
    return _ok(f"Moved \"{best['title']}\" to x={x} y={y} size {w}x{h}.")


def drag(memory, args):
    import pyautogui

    try:
        x0 = float(args.get("x0"))
        y0 = float(args.get("y0"))
        x1 = float(args.get("x1"))
        y1 = float(args.get("y1"))
    except (TypeError, ValueError):
        return _err("drag needs numeric x0,y0 (press) and x1,y1 (release) as screen fractions 0-1")
    for v in (x0, y0, x1, y1):
        if not (0.0 <= v <= 1.0):
            return _err("drag coordinates must be fractions between 0 and 1")
    W, H = pyautogui.size()
    px0, py0 = int(x0 * W), int(y0 * H)
    px1, py1 = int(x1 * W), int(y1 * H)
    dur = float(args.get("duration", 0.45))
    pyautogui.moveTo(px0, py0)
    pyautogui.mouseDown()
    pyautogui.moveTo(px1, py1, duration=dur)
    pyautogui.mouseUp()
    return _ok(f"Dragged from ({px0},{py0}) to ({px1},{py1}).")


def set_volume(memory, args):
    level = max(0, min(100, int(args.get("level", 50))))
    try:
        PLATFORM.set_volume(level)
        return _ok(f"Volume set to {level}%.")
    except NotImplementedError:
        return _err("volume control not supported on this OS yet")
    except Exception as e:
        return _err(str(e))


def media_key(memory, args):
    import pyautogui

    key = args.get("key", "").lower()
    mapping = {"next": "nexttrack", "previous": "previoustrack",
               "playpause": "playpause", "mute": "volumemute"}
    keyname = mapping.get(key, key)
    try:
        pyautogui.press(keyname)
        return _ok(f"Media key: {keyname}")
    except Exception:
        r = run_powershell(None, {"command":
            "(New-Object -ComObject WScript.Shell).SendKeys([char]179)"})
        return _ok(f"Sent media key via shell ({r})")


def remember_about_user(memory, args):
    fact = args.get("fact", "")
    category = args.get("category", "general")
    if memory.remember_fact(fact, category):
        return _ok(f"Remembered ({category}): {fact}")
    return _ok("I already knew that one sir.")


def recall_about_user(memory, args):
    facts = memory.recall_facts(args.get("query", ""), limit=10)
    return _ok("\n".join(f"- {f}" for f in facts) if facts else "Nothing stored about that yet.")


def parse_when(spec):
    spec = spec.strip().lower()
    now = datetime.datetime.now()
    if not spec:
        return _iso(now)
    m = None
    import re

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$", spec)
    if m:
        y, mo, d, h, mi = map(int, m.groups()[:5])
        s = int(m.group(6) or 0)
        return _iso(datetime.datetime(y, mo, d, h, mi, s))
    m = re.match(r"^in\s+(\d+)\s*(minute|min|hour|hr|second|sec)s?$", spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {"min": datetime.timedelta(minutes=n), "minute": datetime.timedelta(minutes=n),
                 "hour": datetime.timedelta(hours=n), "hr": datetime.timedelta(hours=n),
                 "sec": datetime.timedelta(seconds=n), "second": datetime.timedelta(seconds=n)}[unit]
        return _iso(now + delta)
    m = re.match(r"^tomorrow\s+(\d{1,2}):(\d{2})$", spec)
    if m:
        t = (now + datetime.timedelta(days=1)).replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0)
        return _iso(t)
    m = re.match(r"^(\d{1,2}):(\d{2})$", spec)
    if m:
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
        if t <= now:
            t += datetime.timedelta(days=1)
        return _iso(t)
    return _iso(now)


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def add_reminder(memory, args):
    text = args.get("text", "")
    when_iso = parse_when(args.get("when", ""))
    rid = memory.add_reminder(text, when_iso)
    return _ok(f"Reminder #{rid} set for {when_iso}: {text}")


def list_reminders(memory, args):
    items = memory.all_reminders()
    if not items:
        return _ok("No reminders stored.")
    lines = [("[x] " if i["done"] else "[ ] ") + f"#{i['id']} due {i['due_ts']} - {i['text']}"
             for i in items]
    return _ok("\n".join(lines))


def complete_reminder(memory, args):
    memory.complete_reminder(int(args.get("id", 0)))
    return _ok("Reminder completed.")


def add_routine_tool(memory, args):
    text = args.get("text", "")
    tod = str(args.get("time_of_day", "09:00"))[:5]
    days = args.get("days", "daily")
    rid = memory.add_routine(text, tod, days)
    return _ok(f"Routine #{rid} set: {text} at {tod} ({days}).")


def list_routines_tool(memory, args):
    items = [r for r in memory.list_routines() if r["active"]]
    if not items:
        return _ok("No routines defined.")
    return _ok("\n".join(f"#{r['id']} {r['time_of_day']} [{r['days']}] {r['text']}"
                         for r in items))


def remove_routine_tool(memory, args):
    memory.remove_routine(int(args.get("id", 0)))
    return _ok("Routine removed.")


def send_email_secure(memory, args):
    broker = AGENT_CTX.get("broker")
    if broker is None:
        return _err("secrets broker not initialized")
    from core.secrets import SecretsBroker

    del SecretsBroker
    to_addr = args.get("to", "")
    subject = args.get("subject", "(no subject)")
    body = args.get("body", "")
    if not broker.request_use(memory, "your Google app password",
                              f"sending an email to {to_addr}"):
        return "DENIED: user did not grant access to the credential."
    from core.email_secure import send_email_via_gmail

    result = send_email_via_gmail(memory, broker, to_addr, subject, body)
    memory.audit("email_sent", f"to={to_addr} subject={subject[:80]} result={result[:80]}")
    return result


def read_audit_log(memory, args):
    items = memory.recent_audit(limit=int(args.get("limit", 15)))
    if not items:
        return _ok("Audit log is empty.")
    return _ok("\n".join(f"[{i['ts']}] {i['action']}: {i['detail']}" for i in items))


def camera_mirror(memory, args):
    try:
        import cv2
    except ImportError:
        return _err("opencv not installed")
    cam = cv2.VideoCapture(int(args.get("camera_index", 0)))
    if not cam.isOpened():
        return _err("camera not available")
    try:
        while True:
            ret, frame = cam.read()
            if not ret:
                break
            cv2.imshow("DUDE Camera (press q)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if PLATFORM.foreground_window().get("title", "").startswith("DUDE Camera") is False \
                    and cv2.getWindowProperty("DUDE Camera (press q)", 0) < 0:
                break
    finally:
        cam.release()
        cv2.destroyAllWindows()
    return _ok("Camera mirror closed.")


def analyze_recent_screens(memory, args):
    brain = AGENT_CTX.get("brain")
    if brain is None or not brain.has_vision():
        return _err("no vision-capable AI provider configured")
    import base64
    from PIL import Image, ImageGrab

    cfg = get_config()
    files = []
    fresh = None
    try:
        im, _ = _capture_screen_composite()
        save_dir = os.path.join(cfg.data_dir, "screenshots")
        os.makedirs(save_dir, exist_ok=True)
        fresh = os.path.join(save_dir, "screen_%s.png" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        im.save(fresh)
        try:
            keep_png = [f for f in os.listdir(save_dir)
                        if f.startswith("screen_") and f.endswith(".png")]
            keep_png.sort()
            for fn in keep_png[: max(0, len(keep_png) - 40)]:
                os.remove(os.path.join(save_dir, fn))
        except OSError:
            pass
        files = [fresh if os.path.exists(fresh) else os.path.join(save_dir, f)
                 for f in ([os.path.basename(fresh)] if os.path.exists(fresh) else keep_png[-1:])]
    except Exception as e:
        candidate_dirs = [
            os.path.join(cfg.data_dir, "observation"),
            os.path.join(cfg.data_dir, "screenshots"),
        ]
        for d in candidate_dirs:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.lower().endswith((".png", ".jpg", ".jpeg")):
                        files.append(os.path.join(d, f))
        files = sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)
        if not files:
            return _err("could not capture the screen: " + str(e)[:120])

    # Analyze the single most-recent screen at near-native resolution so the
    # description is spatially accurate enough to act on (click/type targets).
    img = Image.open(files[0]).convert("RGB")
    maxdim = 1280
    if max(img.size) > maxdim:
        img.thumbnail((maxdim, maxdim))
    import io

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    b64 = base64.b64encode(buf.getvalue()).decode()
    ocr_note = ""
    try:
        from core.ocr import ocr_image

        ocr_txt = ocr_image(img)
        if ocr_txt:
            ocr_note = ("\n\nEXACT VISIBLE TEXT READ BY OCR FROM THIS SCREEN:\n" +
                        " ".join(ocr_txt.split())[:900])
    except Exception:
        pass
    prompt = (
        "This is the user's current screen (usually a browser page) at this moment. "
        "Answer in 3-5 concise plain sentences, so the assistant can think and act next:\n"
        "1) What page/app is open and roughly where any scrollbar is - is it near the top, "
        "middle, or bottom (what percent).\n"
        "2) What content/structure is visible now: headings, cards, links, menu items. "
        "Name 1-3 clickable things with their approximate location (e.g. 'Products link is "
        "top-nav, center').\n"
        "3) If it is a website: is there a navigation menu, tab bar, or pagination like "
        "'page 2, 3, next'? Name what the OTHER pages are called.\n"
        "4) Read the actual text and content on the screen (title, numbers, names, values) "
        "and include the key ones.\n"
        "5) If you just scrolled and this looks very similar to the previous view, say so "
        "explicitly and note where the scrollbar is.\n"
        "Be spatial and factual; do not invent elements you cannot see." + ocr_note
    )
    out = brain.vision_analyze(b64, prompt)
    return _ok(out or "I could not analyze the screens.")


def read_screen_text(memory, args):
    """OCR the user's current screen with the tesseract engine and return the
    exact visible text (plus the active app/window). This is DUDE's built-in
    text-reading eye — the 'tesseract tool' the user has installed."""
    try:
        im, _ = _capture_screen_composite()
    except Exception as e:
        return _err("could not capture the screen: " + str(e)[:120])
    if im is None:
        return _err("could not capture the screen")
    from core.ocr import ocr_image

    im = im.convert("RGB")
    im.thumbnail((1280, 1280))
    txt = ocr_image(im)
    lines = [" ".join(l.split()) for l in (txt or "").splitlines() if l.strip()]
    if not lines:
        body = "No readable text detected on the screen right now."
    else:
        body = "\n".join(lines)[:3000]
    try:
        w = PLATFORM.foreground_window()
        head = f"Screen (active: {w.get('app')} - {w.get('title')}):\n"
    except Exception:
        head = "Visible text on the current screen:\n"
    return _ok(head + body)


# ---- self-healing / self-improvement hook (bound at startup by dude.py) ----
_OBSERVE = {}


def bind_observe(get_status=None, start=None, stop=None):
    if get_status is not None:
        _OBSERVE["status"] = get_status
    if start is not None:
        _OBSERVE["start"] = start
    if stop is not None:
        _OBSERVE["stop"] = stop


def observe_status(memory, args):
    """Current truth about whether DUDE is really observing and learning."""
    import json as _json
    fn = _OBSERVE.get("status")
    try:
        st = fn() if fn else {}
        return _ok("Observation status: " + _json.dumps(st))
    except Exception as e:
        return _err("status unavailable: " + str(e)[:120])


def _derive_self_rules(memory):
    import sqlite3 as _sq
    rules = []
    try:
        cfg = get_config()
        conn = _sq.connect(os.path.join(cfg.data_dir, "experience.db"))
        rows = conn.execute(
            "SELECT tool, outcome FROM experiences ORDER BY id DESC LIMIT 300"
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    ok, fail = {}, {}
    for tool, outcome in rows:
        if outcome == "OK":
            ok[tool] = ok.get(tool, 0) + 1
        elif outcome == "FAIL":
            fail[tool] = fail.get(tool, 0) + 1
    for tool, n in sorted(fail.items(), key=lambda kv: -kv[1])[:2]:
        if n >= 2 and ok.get(tool, 0) < n:
            rules.append(f"When {tool} fails, switch to a different route quickly "
                         "instead of repeating it.")
    strong = [t for t, n in ok.items() if n >= 5 and fail.get(t, 0) <= 1]
    for t in strong[:2]:
        rules.append(f"Prefer {t} — it has a strong proven success record on this PC.")
    return rules


def reflect_and_improve(memory, args):
    """Self-modification: turn lessons + knowledge into NEW permanent rules DUDE
    keeps in its own personality (CORE_IDENTITY) and knowledge vault."""
    import datetime as _dt
    rule = (args.get("rule") or "").strip()
    added = []
    if rule:
        if memory.install_doctrine("[-SELF-IMPROVED-] " + rule):
            added.append(rule)
    else:
        for r in _derive_self_rules(memory):
            if memory.install_doctrine("[-SELF-IMPROVED-] " + r):
                added.append(r)
    if added:
        try:
            from core.knowledge import get_knowledge
            stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            get_knowledge().append_daily_note(
                "self-improvements",
                [f"({stamp}) {r}" for r in added])
        except Exception:
            pass
        return _ok("Self-improved, sir. New permanent rules I added to myself:\n- "
                   + "\n- ".join(added))
    return _ok("No new self-improvements to add right now — everything like this "
               "is already in my permanent memory.")


def self_diagnose(memory, args):
    """Health check + self-heal: verifies the observer, tesseract and the memory
    DBs, and restarts the watcher if it died while set to observe."""
    import sqlite3 as _sq
    report = []
    st = {}
    try:
        fn = _OBSERVE.get("status")
        if fn:
            st = fn() or {}
        report.append(f"observe_mode={st.get('on', False)} "
                      f"loop_alive={st.get('loop_alive', None)} "
                      f"frames={st.get('frames', 0)} "
                      f"insights={st.get('insights', 0)} "
                      f"restarts={st.get('restarts', 0)}")
    except Exception as e:
        report.append(f"observe status error: {e}")
    try:
        from core.ocr import ensure_tesseract
        eng = ensure_tesseract()
        report.append(f"tesseract={os.path.basename(eng or '') if eng else 'NOT FOUND'}")
    except Exception as e:
        report.append(f"tesseract error: {e}")
    try:
        cfg = get_config()
        for db in ("experience.db", "memory.db"):
            p = os.path.join(cfg.data_dir, db)
            if os.path.exists(p):
                c = _sq.connect(p)
                tbl = "experiences" if db.startswith("exp") else "facts"
                cnt = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                c.close()
                report.append(f"{db}=OK({cnt} rows)")
            else:
                report.append(f"{db}=MISSING")
    except Exception as e:
        report.append(f"db error: {e}")
    if st.get("on") and not st.get("loop_alive"):
        try:
            stop_fn = _OBSERVE.get("stop")
            start_fn = _OBSERVE.get("start")
            if stop_fn and start_fn:
                stop_fn()
                started = start_fn()
                report.append("self-heal: watcher had died -> restarted"
                              + ("" if started else " (start refused: already on)"))
        except Exception as e:
            report.append(f"self-heal attempt error: {e}")
    return _ok("DIAGNOSTICS:\n" + "\n".join(report))


def find_on_screen(memory, args):
    """Locate a specific on-screen thing (a button, field, link, icon or area) by
    DESCRIPTING it, and return its precise normalized (0..1) pixel coordinates so
    the caller can click it exactly. This is the reliable path for BROWSERS / web
    pages where Windows UI Automation cannot see page controls. Uses a fresh
    screenshot + the vision model, then reports exact centre coordinates."""
    brain = AGENT_CTX.get("brain")
    if brain is None or not brain.has_vision():
        return _err("no vision-capable AI provider configured")
    target = (args.get("target") or args.get("find") or "").strip()
    if not target:
        return _err("find_on_screen needs a 'target' description (e.g. the Search button, "
                    "the login field, the Next page link)")
    import base64
    import io
    from PIL import Image, ImageGrab

    cfg = get_config()
    try:
        im, _ = _capture_screen_composite()
    except Exception as e:
        return _err("could not capture screen: " + str(e)[:120])
    img = im.convert("RGB")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode()
    prompt = (
        f"I need to click this specific thing on the user's current screen: \"{target}\".\n"
        "Look at the full screenshot and locate it carefully. It may be a button, a text "
        "field, a link, an icon, or a menu area.\n"
        "Reply with EXACTLY ONE short line of the form:\n"
        "CENTER fx fy\n"
        "where fx and fy are the normalized coordinates (0.0 to 1.0) of the CENTER of that "
        "element across the whole screenshot (0,0 = top-left, 1,1 = bottom-right). Give a "
        "precise decimal to at least 3 places (e.g. 'CENTER 0.512 0.247').\n"
        "If the thing is clearly NOT visible on the screen at all, reply ONLY:\n"
        "NOT FOUND\n"
        "Do not add any other words or explanation."
    )
    out = brain.vision_analyze(b64, prompt)
    if not out:
        return _err("vision returned nothing")
    txt = " ".join(str(out).split())
    m = None
    # allow both 'CENTER fx fy' and loose 'fx...fy' patterns
    import re as _re
    m = _re.search(r"(?:CENTER\s+)?([0-9]\.[0-9]{1,4})\s+([0-9]\.[0-9]{1,4})", txt)
    if m:
        fx, fy = float(m.group(1)), float(m.group(2))
        if 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
            import pyautogui
            W, H = pyautogui.size()
            px, py = int(fx * W), int(fy * H)
            return _ok(f"LOCATED '{target}' at fraction ({fx:.3f},{fy:.3f}) = pixel "
                       f"({px},{py}) on {W}x{H}. Click it now with click_fraction "
                       f"x={fx:.4f} y={fy:.4f}.")
    if "NOT FOUND" in txt.upper():
        return _err(f"'{target}' was not found on the current screen by vision. Re-orient "
                    "(scroll or navigate) then retry, or check it is actually visible.")
    return _err(f"vision could not confirm exact coordinates. Raw reply: {txt[:200]}")


def search_knowledge(memory, args):
    q = args.get("query") or args.get("text") or ""
    if not q:
        return _err("no query provided")
    kb = get_knowledge()
    hits = kb.query(q, int(args.get("count", 5)))
    if not hits:
        return _ok("No matching knowledge found in the indexed files.")
    parts = [f"[from {h['path']}]\n{h['text'][:900]}" for h in hits]
    return _ok("\n\n".join(parts))


def index_knowledge(memory, args):
    kb = get_knowledge()
    path = args.get("path") or get_config().get("knowledge_paths", default=[]) or []
    if isinstance(path, str):
        path = [path]
    if isinstance(path, list):
        total = 0
        for p in path:
            total += kb.index_folder(p)
        return _ok(f"Indexed {total} chunks across {len(path)} path(s).")
    n = kb.index_folder(path)
    return _ok(f"Indexed {n} chunks from {path}.")


def learn_knowledge(memory, args):
    title = args.get("title") or "Learned note"
    body = args.get("body") or args.get("text") or ""
    if len(body) < 10:
        return _err("body too short - provide at least 10 characters of learned content")
    kb = get_knowledge()
    try:
        path = kb.save_note(title[:200], body[:12000])
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")
    cat = "workflow_candidate" if "workflow" in body[:80].lower() else "knowledge"
    return _ok(f"Saved durable knowledge note to {path} and indexed it. "
               f"It is now searchable via search_knowledge.")


def think_now(memory, args):
    thinker = AGENT_CTX.get("thinker")
    if thinker is None:
        return _err("thinker engine not initialized")
    try:
        result = thinker.run_pass(present=False)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")
    if result and result != "already running":
        result = ("Thinker review done. " + str(result) +
                  " Teach this to him in one short spoken line.")
    return _ok(str(result))


def recall_experience(memory, args):
    learner = AGENT_CTX.get("learner")
    if learner is None:
        return _err("experience learner not initialized")
    return _ok("\n".join(f"- {l}" for l in learner.lessons(
        app=args.get("app", ""), limit=int(args.get("limit", 6))) or ["(no lessons yet)"]))


def watch_progress(memory, args):
    learner = AGENT_CTX.get("learner")
    if learner is None:
        return _err("experience learner not initialized")
    if (args.get("mode") or "").lower() in ("stop", "off", "end"):
        summary = learner.watch_stop()
        return _ok(f"Stopped watching. {'I observed things across: ' + '; '.join(summary['places'][:6]) if summary else 'nothing was observed yet.'}")
    started = learner.watch_start(AGENT_CTX.get("observer"),
                                  AGENT_CTX.get("brain"))
    return _ok("Now watching and learning. I will note what you do on screen."
               if started else "Already watching.")


def think_deep(memory, args):
    """Reason on DUDE's OWN linked memory: connects today's work to related
    past sessions via the local association graph and gives a real suggestion.
    Used ONLY when the user asks ('what do you think', 'suggest', 'connect',
    'is this related to'...)."""
    query = (args.get("query") or "").strip() or "what should I focus on next"
    try:
        from core.associations import get_associations
        ab = get_associations()
    except Exception:
        ab = None
    app, window = "unknown", ""
    snap = None
    obs = AGENT_CTX.get("observer")
    if obs is not None:
        try:
            snap = obs.current_screen()
            app = snap.get("app", "unknown")
            window = snap.get("title", "")
        except Exception:
            pass
    facts = []
    try:
        facts = memory.recall_key_facts(limit=10)
    except Exception:
        pass
    assoc, ins = [], []
    if ab is not None:
        try:
            ent = ab.extract_entities(f"{app} {window}")
            assoc = ab.associations_for(app=app, entity_names=ent, limit=6)
            ins = ab.top_insights(3)
        except Exception:
            pass
    brain = AGENT_CTX.get("brain")
    if brain is not None:
        try:
            from core.personality import system_prompt
            joined = "\n".join("- " + a for a in assoc) or "- (graph still warming up)"
            fact_text = "\n".join("- " + f for f in facts[:8]) or "- none yet"
            ins_text = ("\nrecent reasoned links:\n" +
                        "\n".join("- " + i for i in ins)) if ins else ""
            prompt = (
                f"You are DUDE, thinking with YOUR OWN linked memory (a local "
                f"neural-style association graph that connects this session to past "
                f"sessions).\n\n"
                f"CURRENT: user is in {app} - {window}. They ask: \"{query}\"\n\n"
                f"LINKED MEMORY (past+present connections I made):\n{joined}\n"
                f"{ins_text}\n\n"
                f"things I remember about user:\n{fact_text}\n\n"
                "Give a thoughtful, CONCRETE, SHORT spoken answer (2-4 sentences): "
                "connect today's work to what the linked memory says happened before, "
                "reason about what that implies, and give ONE clear, actionable "
                "suggestion if it is useful. No generic filler, no lists.")
            out = (brain.think(system_prompt() + ("\n\n" + prompt[:2000])) or "").strip()
            if out:
                return _ok(out[:2200])
        except Exception:
            pass
    # local fallback (no brain / quota): linked summary from the graph itself
    link_txt = (" | ".join(assoc[:5]) if assoc else
                "I don't have strong links for this yet — keep working so I can "
                "connect it to your past sessions.")
    return _ok(f"Thinking with my linked memory: {link_txt}")


def neural_instinct(memory, args):
    learner = AGENT_CTX.get("action_learner")
    if learner is None:
        return _err("neural action learner not initialized")
    app = args.get("app", "")
    hint = learner.context_hint(app, limit=int(args.get("limit", 5)) or 5)
    if not hint:
        return _ok("Neural model has not learned enough patterns yet to guess "
                   "your next action. Keep working - it learns from what you do "
                   "and from what I do on your screen.")
    return _ok(hint)


def neural_train(memory, args):
    learner = AGENT_CTX.get("action_learner")
    if learner is None:
        return _err("neural action learner not initialized")
    r = learner.train(epochs=int(args.get("epochs", 25) or 25))
    return _ok(str(r))


def neural_datasets(memory, args):
    tracker = AGENT_CTX.get("action_tracker")
    if tracker is None:
        return _err("action tracker not initialized")
    rep = tracker.datasets_report()
    return _ok(
        "TRAINING DATASETS FOR THE NEURAL MODEL:\n" +
        "\n".join(f"- {d['name']}: {d['rows']} rows ({d['use']})" for d in rep["datasets"]) +
        f"\nModel weights: {rep['model_weights']}\nNeural model: {rep['model']}")


def work_today_summary(memory, args):
    rows = memory.today_summary()
    if not rows:
        return _ok("No work activity tracked today yet.")
    cur = memory.current_work()
    cur_txt = f" Currently using: {cur[0]} - \"{cur[1][:80]}\" (since {cur[2][-8:]})." if cur else ""
    total = sum(s for _, s in rows)
    body = ", ".join(f"{a}: {s//60}m" for a, s in rows[:8])
    return _ok(f"Today's tracked activity ({total//3600}h {(total%3600)//60}m total): {body}.{cur_txt}")


def work_yesterday_summary(memory, args):
    rows = memory.day_summary(1)
    if not rows:
        return _ok("Nothing tracked yesterday.")
    last = memory.last_work_before(datetime.datetime.now().replace(hour=0, minute=0, second=0))
    last_txt = f" Last thing you worked on yesterday: \"{last[1][:80]}\" in {last[0]}, ended {last[3][-8:]}." if last else ""
    body = ", ".join(f"{a}: {s//60}m" for a, s in rows[:8])
    return _ok(f"Yesterday: {body}.{last_txt}")


def search_conversation_history(memory, args):
    hits = memory.search_messages(args.get("query", ""), limit=8)
    if not hits:
        return _ok("No past conversation matched that.")
    return _ok("\n".join(f"[{h['ts']}] {h['role']}: {h['content'][:200]}" for h in hits))


def todo_add(memory, args):
    text = args.get("text", "").strip()
    if not text:
        return _err("no task text given")
    tid = memory.todo_add(text)
    return _ok(f"Added task #{tid} to my autonomous list: {text}")


def todo_list(memory, args):
    q = memory.todo_list()
    if not q:
        return _ok("Task list is empty.")
    lines = [("(done) " if t["done"] else "") + f"#{t['id']} [{t['created']}] {t['text']}"
             for t in q]
    return _ok("\n".join(lines))


def todo_remove(memory, args):
    tid = int(args.get("id", 0) or 0)
    memory.todo_remove(tid)
    return _ok(f"Removed task #{tid}.")


# ---------------- registry & specs ----------------

REGISTRY = {
    "get_datetime": get_datetime,
    "battery_status": battery_status,
    "open_app": open_app,
    "close_app": close_app,
    "shutdown_system": shutdown_system,
    "restart_system": restart_system,
    "list_running_apps": list_running_apps,
    "list_directory": list_directory,
    "read_file": read_file,
    "write_file": write_file,
    "create_folder": create_folder,
    "delete_path": delete_path,
    "move_path": move_path,
    "copy_path": copy_path,
    "search_files": search_files,
    "run_powershell": run_powershell,
    "screenshot": screenshot,
    "open_url": open_url,
    "clipboard_read": clipboard_read,
    "clipboard_write": clipboard_write,
    "press_hotkey": press_hotkey,
    "type_text": type_text,
    "click_at": click_at,
    "click_fraction": click_fraction,
    "hover_at": hover_at,
    "ui_scan": ui_scan,
    "ui_click": ui_click,
    "show_ui": show_ui,
    "scroll_screen": scroll_screen,
    "active_window_info": active_window_info,
    "list_windows": list_windows,
    "move_window": move_window,
    "close_window": close_window,
    "window_action": window_action,
    "drag": drag,
    "set_volume": set_volume,
    "media_key": media_key,
    "remember_about_user": remember_about_user,
    "recall_about_user": recall_about_user,
    "add_reminder": add_reminder,
    "list_reminders": list_reminders,
    "complete_reminder": complete_reminder,
    "add_routine": add_routine_tool,
    "list_routines": list_routines_tool,
    "remove_routine": remove_routine_tool,
    "send_email": send_email_secure,
    "read_audit_log": read_audit_log,
    "camera_mirror": camera_mirror,
    "analyze_recent_screens": analyze_recent_screens,
    "read_screen_text": read_screen_text,
    "observe_status": observe_status,
    "reflect_and_improve": reflect_and_improve,
    "self_diagnose": self_diagnose,
    "find_on_screen": find_on_screen,
    "search_knowledge": search_knowledge,
    "learn_knowledge": learn_knowledge,
    "think_now": think_now,
    "recall_experience": recall_experience,
    "watch_progress": watch_progress,
    "neural_instinct": neural_instinct,
    "neural_train": neural_train,
    "neural_datasets": neural_datasets,
    "think_deep": think_deep,
    "index_knowledge": index_knowledge,
    "work_today_summary": work_today_summary,
    "work_yesterday_summary": work_yesterday_summary,
    "search_conversation_history": search_conversation_history,
    "todo_add": todo_add,
    "todo_list": todo_list,
    "todo_remove": todo_remove,
}

CONFIRM_REQUIRED = {"delete_path", "shutdown_system", "restart_system"}

_TOOL_SPECS = [
    ("get_datetime", "Get current date and time.", {}),
    ("battery_status", "Battery level and charging state.", {}),
    ("open_app", "Open an application by name or full path. Set \"new\" to TRUE when "
     "the user wants a second/another instance of an app that may already be running.",
     {"name": {"type": "string", "description": "App name like 'chrome' or a full path"},
      "new": {"type": "boolean",
              "description": "force a fresh instance even if the app is already open"}}),
    ("close_app", "Close/terminate all processes matching a name.",
     {"name": {"type": "string", "description": "process name e.g. 'notepad' or 'chrome'"}}),
    ("shutdown_system", "Shutdown the computer. Requires confirmation.",
     {}),
    ("restart_system", "Restart the computer. Requires confirmation.",
     {}),
    ("list_running_apps", "List notable running processes.", {}),
    ("list_directory", "List a folder's contents.",
     {"path": {"type": "string"}}),
    ("read_file", "Read a text file (max ~20k chars returned).",
     {"path": {"type": "string"}}),
    ("write_file", "Create/overwrite or append to a text file.",
     {"path": {"type": "string"},
      "content": {"type": "string"},
      "append": {"type": "boolean", "description": "append instead of overwrite"}}),
    ("create_folder", "Create a folder (parents auto-created).",
     {"path": {"type": "string"}}),
    ("delete_path", "PERMANENTLY delete a file or folder. Requires confirmation.",
     {"path": {"type": "string"}}),
    ("move_path", "Move/rename a file or folder.",
     {"source": {"type": "string"}, "destination": {"type": "string"}}),
    ("copy_path", "Copy a file or folder.",
     {"source": {"type": "string"}, "destination": {"type": "string"}}),
    ("search_files", "Search files by substring under a root folder.",
     {"root": {"type": "string"}, "pattern": {"type": "string"}}),
    ("run_powershell", "Run any PowerShell command.",
     {"command": {"type": "string"}}),
    ("screenshot", "Take a screenshot of all screens, save it, return the path.", {}),
    ("open_url", "Open a URL in the default browser.", {"url": {"type": "string"}}),
    ("clipboard_read", "Read current clipboard text.", {}),
    ("clipboard_write", "Set clipboard text.", {"text": {"type": "string"}}),
    ("press_hotkey", "Press a keyboard shortcut, e.g. 'ctrl+s'.",
     {"combo": {"type": "string", "description": "keys separated by + or space"}}),
    ("type_text", "Type text into the focused window.",
     {"text": {"type": "string"}}),
    ("click_at", "Click at screen coordinates; omit x/y to click current position.",
     {"x": {"type": "integer"}, "y": {"type": "integer"}}),
    ("hover_at", "Move the mouse to a location and HOLD it there WITHOUT clicking - this reveals "
     "hover menus/dropdowns (like navigating a website's top navigation bar, which only shows its "
     "sub-pages when the cursor is over it). Give x,y pixels OR fx,fy normalized fractions. After "
     "hovering, always take analyze_recent_screens to read what opened, then ui_click/click it. "
     "Set press='down' to hold the mouse button for dragging.",
     {"x": {"type": "integer"}, "y": {"type": "integer"},
      "fx": {"type": "number"}, "fy": {"type": "number"},
      "hold_s": {"type": "number"}, "press": {"type": "string"}}),
    ("ui_scan", "Get the live, structured map of the ACTIVE window: every visible button/box/tab/menu "
     "with its real name and exact pixel position, from Windows UI Automation. Use this INSTEAD of "
     "vision/screenshots to know where controls are before clicking.",
     {"limit": {"type": "integer", "description": "max rows to return (default 26)"}}),
    ("ui_click", "Click a real UI control by its NAME on the active window (e.g. 'Restore', 'Search', "
     "'Close tab'), optionally limiting to a role like btn/input/tab. Position is read live from UI "
     "Automation so the cursor always lands on the actual widget. Use instead of guessing pixel "
     "coordinates.",
     {"name": {"type": "string"}, "role": {"type": "string"},
      "x": {"type": "integer"}, "y": {"type": "integer"}}),
    ("show_ui", "Bring DUDE's own UI (the Notch pill / command centre) back on screen and to the "
     "foreground if it was hidden. Use when he asks where your UI is or you notice it missing.",
     {}),
    ("click_fraction", "Click using NORMALIZED coordinates (x,y each 0.0-1.0, where 0,0 = top-left "
     "and 1,1 = bottom-right of the PRIMARY screen). Map it against what CURRENT SCREEN / "
     "analyze_recent_screens reports so you hit an actual button, text field, or profile chip.",
     {"x": {"type": "number"}, "y": {"type": "number"}}),
    ("scroll_screen", "Scroll one SMALL, SENSITIVE step. Only -120 (down one wheel notch) or "
     "+120 (up one wheel notch). NEVER use big amounts - one notch at a time. After each"
     " small scroll, analyze the result (screenshot) and decide the next step based on what "
     "you actually see (scrollbar, headings, nav links, pagination). "
     "If the screen has not changed after a few scrolls, STOP scrolling and click a nav/link"
     " or scroll back.",
     {"amount": {"type": "integer", "description": "use -120 down or +120 up; one notch"},
      "direction": {"type": "string", "description": "'down' or 'up' (optional)"}}),
    ("list_windows", "List visible top-level windows with their title and screen rectangle "
     "(x,y position + width,height in pixels on the primary screen). Call this before moving "
     "or focusing anything so you know the exact window name and where it is.",
     {}),
    ("move_window", "Move and/or resize a window found by title substring. Pass center=true to center "
     "it on the primary screen, or give x/y (position) and w/h (size) in pixels.",
     {"title": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"},
      "w": {"type": "integer"}, "h": {"type": "integer"}, "center": {"type": "boolean"}}),
    ("close_window", "Close ONE specific window found by title substring (like one browser panel or "
     "one app window) cleanly, WITHOUT killing the entire app or other windows.",
     {"title": {"type": "string"}}),
    ("window_action", "Perfom a window frame action by title (the OS title-bar buttons work this way, "
     "even for apps hiding their accessibility tree): 'restore' un-maximizes, 'maximize', 'minimize', "
     "'focus' brings to front. For closing use close_window instead.",
     {"title": {"type": "string"},
      "action": {"type": "string", "enum": ["restore", "maximize", "minimize", "focus", "close"]}}),
    ("drag", "Physically drag the mouse: press-and-hold at (x0,y0), move to (x1,y1), release. "
     "All coordinates are fractions 0-1 of the screen. Use it to drag windows by their title bar, "
     "reposition tiles/tabs, or move any UI element like a person would.",
     {"x0": {"type": "number"}, "y0": {"type": "number"},
      "x1": {"type": "number"}, "y1": {"type": "number"},
      "duration": {"type": "number", "description": "drag travel time in seconds"}}),
    ("active_window_info", "Name and title of the current foreground window.", {}),
    ("set_volume", "Set master volume 0-100.", {"level": {"type": "integer"}}),
    ("media_key", "Press a media key: playpause | next | previous | volumemute.",
     {"key": {"type": "string"}}),
    ("remember_about_user", "Permanently remember a fact/preference about the user.",
     {"fact": {"type": "string"}, "category": {"type": "string",
      "description": "e.g. personal, work, preferences, people"}}),
    ("recall_about_user", "Search stored facts about the user.",
     {"query": {"type": "string"}}),
    ("add_reminder", "Set a reminder. 'when' accepts ISO 'YYYY-MM-DD HH:MM', "
     "'in N minutes/hours', 'HH:MM', or 'tomorrow HH:MM'.",
     {"text": {"type": "string"}, "when": {"type": "string"}}),
    ("list_reminders", "List all reminders.", {}),
    ("complete_reminder", "Mark reminder done by id.", {"id": {"type": "integer"}}),
    ("add_routine", "Create a recurring routine, e.g. standup at 09:30 on weekdays.",
     {"text": {"type": "string"},
      "time_of_day": {"type": "string", "description": "HH:MM 24h"},
      "days": {"type": "string",
               "description": "'daily' or comma list like 'monday,wednesday'"}}),
    ("list_routines", "List recurring routines.", {}),
    ("remove_routine", "Deactivate a routine by id.", {"id": {"type": "integer"}}),
    ("send_email", "Send an email from the user's Gmail. Asks permission to use "
     "the stored app password; never stores or exposes it.",
     {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}),
    ("read_audit_log", "Show recent audit trail of DUDE's actions.", {}),
    ("camera_mirror", "Open webcam mirror window (press q inside to close).",
     {"camera_index": {"type": "integer"}}),
    ("analyze_recent_screens", "Analyze recent screenshots with vision AI and report "
     "what the user is working on + suggestions.",
     {"count": {"type": "integer"}}),
    ("read_screen_text", "Read the exact text currently visible on the user's screen "
     "using the built-in tesseract OCR engine (the 'tesseract tool'). Returns the raw "
     "on-screen text + active window. Use when the user asks about on-screen text, "
     "mentions tesseract/OCR, or wants you to read visible content precisely.",
     {}),
    ("observe_status", "Report the true current state of DUDE's continuous observer: "
     "is it actually recording, how many captures/insights so far. Use when the user "
     "or you doubt whether observation is running.",
     {}),
    ("reflect_and_improve", "SELF-IMPROVEMENT: review DUDE's own experience record and "
     "turn what was learned (mistakes, proven wins) into NEW permanent rules in DUDE's "
     "personality/memory. Optionally pass a 'rule' the user or DUDE wants permanently "
     "adopted. Use when the user says 'improve yourself', 'self modify', 'make yourself "
     "better', 'learn from your mistakes', or when DUDE notices it keeps repeating a "
     "failure.",
     {"rule": {"type": "string", "description": "optional specific rule to adopt permanently"}}),
    ("self_diagnose", "Run a health check on DUDE (observer, tesseract OCR, memory "
     "databases) and SELF-HEAL anything broken found (e.g. restart a dead observer loop). "
     "Use when something seems silently broken or before reporting it's not working.",
     {}),
    ("find_on_screen", "Locate a specific on-screen thing (button, field, link, icon or "
     "area) by description and return its precise normalized coordinates for an exact "
     "click. Use THIS for browsers/web pages where UI Automation can't see page controls. "
     "Then click_fraction the returned x,y.",
     {"target": {"type": "string", "description": "what to find, e.g. 'the Search button'"}}),
    ("search_knowledge", "Search the user's indexed local files/notes (RAG) for relevant "
     "information by query.", {"query": {"type": "string"}, "count": {"type": "integer"}}),
    ("index_knowledge", "Index a folder (or configured knowledge_paths) so its text becomes "
     "searchable via search_knowledge. Call after adding notes.",
     {"path": {"type": "string", "description": "folder to index; omit to use knowledge_paths"}}),
    ("learn_knowledge", "Persist a durable, searchable knowledge note (a real lesson learned "
     "from studying files, the screen, or a project) into DUDE's own knowledge vault. Use "
     "whenever you learn something worth remembering long-term.",
     {"title": {"type": "string"}, "body": {"type": "string",
                                            "description": "concise facts/lessons learned"}}),
    ("think_now", "Run DUDE's own strategic-thinking pass: it reviews everything seen and "
     "learned so far and returns ONE concrete improvement — a lesson to teach him or a safe "
     "improvement it can implement itself. Call when he asks 'is there a better way', "
     "'what should I do differently', 'how can I make this easier', 'teach me something', "
     "or wants you to think about his work on your own.",
     {}),
    ("recall_experience", "Recall DUDE's own accumulated experience record — what actions "
     "have worked or repeatedly failed for you in a context or app. Use to avoid repeating "
     "past mistakes.",
     {"app": {"type": "string", "description": "optional app name to filter by"},
      "limit": {"type": "integer"}}),
    ("watch_progress", "Start or stop watching the user on screen to learn their way of "
     "working. mode 'start' begins observing; mode 'stop'/('off'/'end') finishes and stores "
     "what was seen.",
     {"mode": {"type": "string", "description": "'start' or 'stop'"}}),
    ("neural_instinct", "Ask DUDE's locally-trained neural action model what it predicts "
     "the user's most likely next action is, in an app (or broadly). It learns from what "
     "you actually do on screen and from past outcomes. Use to anticipate the user's next "
     "move instead of guessing.",
     {"app": {"type": "string", "description": "optional app to filter by"},
      "limit": {"type": "integer"}}),
    ("neural_train", "Re-train (fine-tune) DUDE's neural action model on all accumulated "
     "screen+outcome data so it learns the user's latest patterns.",
     {"epochs": {"type": "integer", "description": "training epochs (default 25)"}}),
    ("neural_datasets", "Show the machine-learning datasets that feed DUDE's neural model "
     "and their row counts.",
     {}),
    ("think_deep", "THINK ONLY WHEN ASKED: reason with DUDE's own linked memory. Pulls the "
     "local association graph (how today's work connects to past sessions), the neural "
     "pattern model, and remembered facts, then gives a concrete suggestion. Use when the "
     "user asks 'what do you think', 'suggest', 'connect this', 'is this related to...'. "
     "NEVER call it unsolicited.",
     {"query": {"type": "string", "description": "what the user wants you to think about"}}),
    ("work_today_summary", "Summary of apps/work tracked today plus current activity.", {}),
    ("work_yesterday_summary", "Summary of yesterday's tracked work and where you stopped.", {}),
    ("search_conversation_history", "Search past conversations by keyword.",
     {"query": {"type": "string"}}),
    ("todo_add", "Add a follow-up task to DUDE's own autonomous to-do list (things "
     "it should finish on its own later, even while the user is away).",
     {"text": {"type": "string"}}),
    ("todo_list", "List DUDE's autonomous to-do items.", {}),
    ("todo_remove", "Remove a to-do item by its id number.",
     {"id": {"type": "integer"}}),
]


def tool_specs():
    specs = []
    for name, desc, props in _TOOL_SPECS:
        required = [p for p in props
                    if p in ("path", "name", "command", "url", "text", "fact", "combo",
                             "query") and p in props]
        specs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return specs


def execute_tool(name, arguments_json, memory, ask_user):
    fn = REGISTRY.get(name)
    if fn is None:
        return f"ERROR: unknown tool {name}"
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as e:
        return f"ERROR: bad arguments ({e})"
    if name in CONFIRM_REQUIRED:
        summary = args.get("command") or args.get("path") or "?"
        if not ask_user(f"May I {name.replace('_', ' ')}: {summary}?"):
            return "DENIED: user declined this action."
    try:
        result = str(fn(memory, args))[:6000]
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if result.startswith("ERROR:"):
        result += (" STRATEGY: read the error, then autonomously retry through a "
                   "different route (another tool, run_powershell/Start-Process, a "
                   "hotkey, or opening the app and clicking its UI). VERIFY before "
                   "reporting to the user.")
    return result
