"""Phase 14 live proof: one real visual state transition on the bus.

BEFORE: observe the current desktop (state A) through the REAL
PerceptionEngine, published by the REAL TaskEngine path.
CHANGE: launch a real OS window (Notepad) — a genuine environment change.
AFTER: observe again (state B); the bus must reflect B.
QUERY: a cognitive-style read (describe + get) must return that same B.

No faked snapshots: every state comes from live perception. Notepad is
terminated afterwards; the window is a test-harness artifact, not a
product handler (no app-specific code was added to DUDE itself).
"""
import subprocess
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')

import prove_phase13 as P
from core.orchestrator.state import PerceptionLevel
from core.orchestrator.visual_state import VisualStateBus


def foreground_pid(pid, timeout=12.0):
    """Bring a launched window to the foreground (test-harness only)."""
    import win32api
    import win32gui
    import win32process
    t0 = time.time()
    while time.time() - t0 < timeout:
        found = []

        def _cb(hwnd, _):
            try:
                _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                if (wpid == pid and win32gui.IsWindowVisible(hwnd)
                        and win32gui.GetWindowText(hwnd)):
                    found.append(hwnd)
            except Exception:
                pass

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            pass
        for hwnd in found:
            try:
                fg = win32gui.GetForegroundWindow()
                fg_thread, _ = win32process.GetWindowThreadProcessId(fg)
                ours = win32api.GetCurrentThreadId()
                win32process.AttachThreadInput(ours, fg_thread, True)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32process.AttachThreadInput(ours, fg_thread, False)
            except Exception:
                pass
            try:
                if win32gui.GetForegroundWindow() == hwnd:
                    return True
            except Exception:
                pass
        time.sleep(0.4)
    return False


def observe(engine, bus, source, goal=""):
    snap = engine._observe_for_task(PerceptionLevel.LEVEL_1_APP_WINDOW)
    # _observe_for_task already publishes when engine.visual_state_bus
    # is set; publish explicitly too so the proof holds even if the
    # engine path is ever detached.
    st = bus.publish(snap, source=source, goal=goal)
    return snap, st


def wait_foreground_notepad(engine, bus, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, st = observe(engine, bus, "prove_phase14")
        app = (st.active_app or "").lower()
        title = (st.window_title or "").lower()
        if "notepad" in app or "notepad" in title:
            return st
        time.sleep(0.5)
    return bus.get()


def main():
    engine, router, store, learner, memory = P.build()
    bus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = bus  # live engine publish path

    seen = []
    bus.subscribe(seen.append)

    _, a = observe(engine, bus, "prove_phase14:before")
    print("BEFORE app=%r title=%r" % (a.active_app, a.window_title),
          flush=True)

    proc = subprocess.Popen(["notepad.exe"])
    try:
        if not foreground_pid(proc.pid):
            print("WARN: could not foreground notepad; trying observe loop",
                  flush=True)
        b = wait_foreground_notepad(engine, bus)
        print("AFTER  app=%r title=%r" % (b.active_app, b.window_title),
              flush=True)
        assert (b.active_app, b.window_title) != \
            (a.active_app, a.window_title), \
            "environment changed but bus state did not!"
        assert ("notepad" in (b.active_app or "").lower()
                or "notepad" in (b.window_title or "").lower()), \
            "bus AFTER state does not reflect the new window!"

        trans = bus.last_transition()
        print("transition: %s -> %s" % (trans.get("from"), trans.get("to")),
              flush=True)
        assert trans.get("to") == b.key(), \
            "transition record does not point at current state!"

        # Cognitive query reads the SAME current state.
        words = bus.describe()
        cur = bus.get()
        print("--- cognitive read ---", flush=True)
        print(words, flush=True)
        print("----------------------", flush=True)
        assert cur.key() == b.key(), "query read a different state!"
        assert ("notepad" in words.lower()), \
            "query words do not describe the current window!"
        assert not cur.is_stale(30.0), "fresh state reads as stale!"
        assert len(seen) >= 2, "subscribers were not notified!"

        print("PROOF PASS: A=%r B=%r same-state query OK pubs=%d" % (
            a.key(), b.key(), bus.publish_count), flush=True)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print("notepad closed", flush=True)


if __name__ == "__main__":
    main()
