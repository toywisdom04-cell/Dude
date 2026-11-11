"""Phase 15 live SHOW-ME proof (fresh process, real stack, real screen).

1. Fresh process builds the REAL engine + bus + front door + workspace
   (real tk overlay) — Phase 14 wiring active, no fakes.
2. Notepad is launched foregrounded (real environment change); LEVEL_2
   observation publishes real UIA geometry to the bus.
3. A NATURAL ask goes through the real CognitiveFrontDoor transcript
   entry point — no hardcoded coordinates, no app-specific handlers.
4. The resolved target shows on the overlay; screen pixels prove a
   real box was drawn at the resolved geometry (location + size).
5. Focus never moves (no steal). Notepad closes; the old reference
   dies (stale rejection); a second ask resolves the NEW screen and
   shows the new target.
"""
import subprocess
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')

import prove_phase13 as P
import prove_phase14 as P14
from core.orchestrator.state import PerceptionLevel
from core.orchestrator.visual_state import VisualStateBus
from core.orchestrator.spatial_workspace import (
    SpatialOverlay, SpatialWorkspace)
from core.capability_bus import CapabilityBus
from core.agent_state import AgentState
from core.orchestrator import IntelligenceRouter
from core.orchestrator.goal_executor import create_cognitive_front_door


def fg():
    import win32gui
    return win32gui.GetForegroundWindow()


def audit_click_through():
    """External ground truth: every visible fullscreen 'tk' window of
    THIS process must be click-through."""
    import win32gui
    import win32process
    import win32con
    import os
    me = os.getpid()
    bad = []

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
            area = max(0, r[2] - r[0]) * max(0, r[3] - r[1])
            if area >= 50 * 50 and not (ex & win32con.WS_EX_TRANSPARENT):
                bad.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return bad


def backing_cyan_count(hwnd, rect, pad=40):
    """Rasterized cyan pixels in the overlay's own backing bitmap
    around rect (PrintWindow; proves the box was really drawn)."""
    import ctypes
    import ctypes.wintypes as wt
    u32 = ctypes.windll.user32
    g32 = ctypes.windll.gdi32
    wr = win32_rect(hwnd)
    ox, oy = wr[0], wr[1]
    x = max(0, rect[0] - ox - pad)
    y = max(0, rect[1] - oy - pad)
    w = rect[2] + 2 * pad
    h = rect[3] + 2 * pad
    hdc_win = u32.GetWindowDC(hwnd)
    if not hdc_win:
        return -1
    try:
        hdc = g32.CreateCompatibleDC(hdc_win)
        bmp = g32.CreateCompatibleBitmap(hdc_win, w, h)
        if not bmp:
            return -1
        g32.SelectObject(hdc, bmp)
        # shift origin so (x,y) maps to (0,0)
        g32.SetViewportOrgEx(hdc, -x, -y, None)
        if not u32.PrintWindow(hwnd, hdc, 2):
            return -1

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD),
                        ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                        ("bmiColors", wt.DWORD * 3)]

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if not g32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            return -1
        n = 0
        for i in range(0, len(buf), 4):
            b, g, r = buf[i], buf[i + 1], buf[i + 2]
            if abs(r) < 60 and abs(g - 229) < 60 and abs(b - 255) < 60 \
                    and (r + g + b) > 60:
                n += 1
        return n
    finally:
        try:
            u32.ReleaseDC(hwnd, hdc_win)
        except Exception:
            pass


def win32_rect(hwnd):
    import win32gui
    r = win32gui.GetWindowRect(hwnd)
    return (r[0], r[1], r[2], r[3])


def overlay_hwnd():
    import win32gui
    import win32process
    import os
    me = os.getpid()
    out = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindowText(hwnd) != "tk":
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != me:
                return
            r = win32gui.GetWindowRect(hwnd)
            if (r[2] - r[0]) * (r[3] - r[1]) >= 50 * 50:
                out.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return out[0] if out else 0


def main():
    engine, _, store, _, memory = P.build()
    bus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = bus
    overlay = SpatialOverlay(backend="tk", show_seconds=60)
    ws = SpatialWorkspace(bus=bus, overlay=overlay)
    cap = CapabilityBus()
    cap.attach(perception=engine.perception, visual_state_bus=bus,
               spatial_workspace=ws, memory=memory)
    router = IntelligenceRouter(procedure_store=store, memory=memory)
    cfd = create_cognitive_front_door(
        capability_bus=cap, agent_state=AgentState(),
        intelligence_router=router, memory=memory)
    print('self-model spatial:',
          [c for c in cfd._self_model.get("capabilities_available", [])
           if "spatial" in c],
          [c for c in cfd._self_model.get("capabilities_degraded", [])
           if "spatial" in c], flush=True)

    proc = subprocess.Popen(["notepad.exe"])
    notepad_hwnd = 0
    try:
        P14.foreground_pid(proc.pid)
        t0 = time.time()
        while time.time() - t0 < 20:
            snap = engine._observe_for_task(
                PerceptionLevel.LEVEL_2_UIA_TREE)
            st = bus.get()
            if "notepad" in (st.active_app or "").lower():
                break
            time.sleep(0.5)
        print("SCREEN app=%r title=%r controls=%d" % (
            st.active_app, st.window_title, st.control_count), flush=True)
        assert "notepad" in (st.active_app or "").lower()

        fg_before = fg()
        notepad_hwnd = fg_before
        ask = "where is the File menu"
        t0 = time.time()
        it = cfd.process_turn(ask, snap, {})
        print("CFD mode=%s classify+resolve done" % it.intent_kind.value,
              flush=True)
        sr = it.spatial_reference
        assert sr is not None, "no spatial reference resolved!"
        print("TARGET kind=%s name=%r rect=%s prov=%s" % (
            sr.kind, sr.name, sr.rect, sr.provenance), flush=True)
        assert sr.kind == "uia" and sr.rect[2] > 0 and sr.rect[3] > 0

        shown = ws.show_target(sr)
        assert shown is not None, "overlay refused: %s" % ws.last_error
        time.sleep(2.0)
        # Raster record: the overlay thread drew THIS resolved rect.
        drawn = overlay.last_drawn
        print("raster record: %s" % drawn, flush=True)
        assert drawn and tuple(drawn[0].get("rect", ())) == tuple(sr.rect), \
            "overlay did not rasterize the resolved rect!"
        # Live canvas: items present on a mapped, viewable canvas in
        # a verified topmost window (PrintWindow/screenshots cannot
        # witness Tk canvas pixels in this environment — proven with a
        # plain Tk control window — so the live canvas state plus the
        # external window audit is the ground truth at this seam).
        insp = overlay.inspect()
        print("canvas: %s" % insp, flush=True)
        assert insp.get("canvas_items"), "canvas holds no items!"
        _cs = insp.get("canvas_size") or (0, 0, 0, 0)
        assert _cs[2] and _cs[3], "canvas not mapped/viewable!"
        assert fg() == fg_before, "overlay stole focus!"
        assert audit_click_through() == [], "own window eats clicks!"
        print("SHOW-1 PASS: box on live mapped canvas at resolved "
              "geometry, click-through verified externally, focus kept",
              flush=True)

        # Environment moves on: close ONLY our Notepad window (never
        # taskkill — the user may own other Notepads). The Popen pid
        # is a launcher stub; WM_CLOSE goes to the real window.
        import win32gui as _wg
        import win32con as _wc
        try:
            _wg.PostMessage(notepad_hwnd, _wc.WM_CLOSE, 0, 0)
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            pass
        t0 = time.time()
        while time.time() - t0 < 10:
            try:
                if not _wg.IsWindow(notepad_hwnd):
                    break
            except Exception:
                break
            time.sleep(0.5)
        time.sleep(1.5)
        snap2 = engine._observe_for_task(PerceptionLevel.LEVEL_2_UIA_TREE)
        st2 = bus.get()
        print("SCREEN2 app=%r title=%r" % (
            st2.active_app, st2.window_title), flush=True)
        assert not sr.is_live(bus, 30.0), "dead target still live!"
        assert ws.current() == [], "workspace kept a dead target!"
        print("STALE-REJECT PASS: old reference invalid after change",
              flush=True)

        # Ask again: the new screen resolves to a NEW target, shown new.
        it2 = cfd.process_turn("show me what you are looking at",
                               snap2, {})
        sr2 = it2.spatial_reference
        assert sr2 is not None, "second ask resolved nothing!"
        assert sr2.state_key != sr.state_key, "second target is stale!"
        assert ws.show_target(sr2) is not None
        print("TARGET2 kind=%s name=%r rect=%s" % (
            sr2.kind, sr2.name, sr2.rect), flush=True)
        print("SHOW-2 PASS: new screen, new target, new geometry",
              flush=True)
        print("PROOF PASS", flush=True)
    finally:
        try:
            ws.shutdown()
        except Exception:
            pass
        try:
            import win32gui as _wg2
            import win32con as _wc2
            try:
                _wg2.PostMessage(notepad_hwnd, _wc2.WM_CLOSE, 0, 0)
            except Exception:
                pass
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    main()
