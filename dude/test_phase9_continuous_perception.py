#!/usr/bin/env python
"""Phase 9 Part A test: CONTINUOUS PERCEPTION (real desktop).

The service under test is an always-on layer over the existing
PerceptionEngine: WinEvents + cheap polling (fast lane), UIA refresh +
dialog classification on change (medium lane), explicit deep observes
(slow lane), TTL screenshot ring, sanitized structured memory.

Allowed production tools for arrange/teardown: open_app (new test-owned
Notepad only). BANNED: run_powershell, write_file, any direct UI
manipulation beyond closing the exact test-owned HWND at teardown.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

BANNED_TOOLS = {"run_powershell", "write_file"}
FRAME_TTL = 4.0


def windows_of(exe_name):
    import win32gui
    import win32process
    import psutil
    from core.orchestrator.app_instances import is_app_window
    out = set()

    def cb(h, acc):
        if not win32gui.IsWindowVisible(h):
            return True
        try:
            pid = win32process.GetWindowThreadProcessId(h)[1]
            exe = (psutil.Process(pid).name() or '').lower()
            if exe != exe_name:
                return True
            title = win32gui.GetWindowText(h) or ''
            wclass = win32gui.GetClassName(h) or ''
            if is_app_window(exe, wclass, title):
                acc.add(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, out)
    return out


def snapshot_desktop():
    return {
        'notepad.exe': windows_of('notepad.exe'),
        'explorer.exe': windows_of('explorer.exe'),
        'comet.exe': windows_of('comet.exe'),
    }


def modal_guard():
    import win32gui
    fg = win32gui.GetForegroundWindow()
    try:
        assert win32gui.GetClassName(fg) != '#32770', \
            'a modal dialog owns the foreground; clear it, then rerun'
    except AssertionError:
        raise
    except Exception:
        pass


def build_stack():
    from core.screentree import ScreenMap
    from core.tools import init_screentree
    from core.memory import Memory
    from core.orchestrator import PerceptionEngine
    from core.orchestrator.perception_service import PerceptionService
    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(3)
    assert smap.available(), 'UIA screen map unavailable'

    def _ocr_fn():
        try:
            from core.tools import _capture_screen_composite
            from core.ocr import ocr_image
            img, _ = _capture_screen_composite()
            if img is None:
                return None
            return {"text": ocr_image(img) or "", "regions": []}
        except Exception:
            return None

    def _capture_fn():
        try:
            from core.tools import _capture_screen_composite
            img, _ = _capture_screen_composite()
            return img
        except Exception:
            return None

    memory = Memory()
    perception = PerceptionEngine(
        get_screentree=lambda: smap,
        get_ocr_fn=_ocr_fn,
        get_capture_fn=_capture_fn,
    )
    service = PerceptionService(
        perception, capture_fn=_capture_fn, memory=memory,
        fast_hz=10.0, idle_hz=4.0,
        frame_ttl_seconds=FRAME_TTL, max_frames=4)
    return {"service": service, "perception": perception,
            "memory": memory, "smap": smap}


class Spy:
    def __init__(self):
        from core.orchestrator import action_executor as ae_mod
        self.ae_mod = ae_mod
        self.real = ae_mod.execute_tool
        self.tools = []

    def __enter__(self):
        def recording(name, args_json, mem, ask_user):
            self.tools.append(name)
            return self.real(name, args_json, mem, ask_user)

        self.ae_mod.execute_tool = recording
        return self

    def __exit__(self, *a):
        self.ae_mod.execute_tool = self.real

    def assert_clean(self):
        bad = sorted({c for c in self.tools if c in BANNED_TOOLS})
        assert not bad, 'BYPASS DETECTED: task used %s' % bad


def wait_for(pred, timeout, desc, poll=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(poll)
    raise AssertionError(f'timeout waiting for: {desc}')


def main():
    before = snapshot_desktop()
    modal_guard()
    stack = build_stack()
    svc = stack["service"]
    memory = stack["memory"]
    test_hwnds = set()

    def close_exact(hwnd):
        import win32gui
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            pass

    try:
        with Spy() as spy:
            from core.orchestrator import action_executor as ae_mod
            import json

            def tool(name, args):
                out = ae_mod.execute_tool(
                    name, json.dumps(args), memory, lambda *a: True)
                assert isinstance(out, str) and not out.startswith(
                    ("ERROR", "DENIED")), f'{name} failed: {out[:300]}'
                return out

            svc.start()
            assert svc._fast_thread and svc._fast_thread.is_alive(), \
                'fast loop thread not running'

            # BASELINE: service observes the real foreground by itself.
            base = wait_for(lambda: svc.current_observation(), 15,
                            'initial observation')
            print(f'BASELINE app={base.active_app!r} '
                  f'title={base.window_title!r} source={base.source} '
                  f'events_enabled={svc.events_enabled}', flush=True)
            assert base.active_app and base.active_app != 'unknown', \
                'service never identified the foreground app'
            n0 = svc.counters()["observations_created"]

            # TEST 1 — window appeared: open ONE new test-owned Notepad
            # through the production open_app tool.
            r = tool("open_app", {"name": "Notepad", "new": True})
            assert 'notepad' in r.lower() or 'opened' in r.lower() or \
                'focus' in r.lower(), f'open_app odd result: {r[:200]}'
            time.sleep(2.5)
            after = windows_of('notepad.exe')
            new = sorted(after - before['notepad.exe'])
            assert new, 'no new Notepad window appeared after open_app'
            test_hwnds.update(new)
            th = new[0]
            print(f'TEST1 new test-owned notepad hwnd={th}', flush=True)

            def appeared():
                # Background birth first: exact-HWND census evidence.
                for e in svc.recent_window_events():
                    if e["hwnd"] == th and e["kind"] == "appeared":
                        o = svc.current_observation()
                        return ("birth", e, o)
                # Foreground birth: the new window owns the foreground.
                o = svc.current_observation()
                if o is not None and o.hwnd == th:
                    return ("foreground", None, o)
                return None

            kind, ev, obs = wait_for(
                appeared, 20, f'service observation of new notepad hwnd={th}')
            if kind == "birth":
                print(f'TEST1 FULL PASS (background birth) {ev}', flush=True)
                if obs is not None:
                    print(f'TEST1 latest change={obs.change} '
                          f'related={obs.related!r}', flush=True)
            else:
                print(f'TEST1 FULL PASS (foreground) change={obs.change} '
                      f'source={obs.source} title={obs.window_title!r} '
                      f'related={obs.related!r}', flush=True)
                assert obs.change in ('action_relevant', 'task_relevant',
                                      'user_relevant', 'low'), \
                    f'unexpected change class {obs.change}'
            assert svc.counters()["observations_created"] > n0

            # TEST 2 — window closed: close ONLY the exact test-owned HWND,
            # service must notice the disappearance.
            n1 = svc.counters()["observations_created"]
            close_exact(th)
            import win32gui
            wait_for(lambda: not win32gui.IsWindow(th), 10,
                     'test-owned notepad to close')
            print('TEST2 test-owned window confirmed closed', flush=True)

            def gone_or_new_obs():
                for e in svc.recent_window_events():
                    if e["hwnd"] == th and e["kind"] == "closed":
                        return e
                return None

            obs2 = wait_for(gone_or_new_obs, 20,
                            'service closed-event for test-owned hwnd')
            print(f'TEST2 FULL PASS (background death) {obs2}', flush=True)
            o2 = svc.current_observation()
            if o2 is not None:
                print(f'TEST2 latest change={o2.change} source={o2.source} '
                      f'related={o2.related!r}', flush=True)
            test_hwnds.discard(th)

            # TEST 3 — TTL ring: frames expire, nothing accumulates.
            data = svc.capture_frame("p9test")
            assert data, 'capture_frame returned nothing'
            assert svc._frames.live_count() >= 1
            time.sleep(FRAME_TTL + 1.5)
            expired = svc._frames.sweep()
            assert svc._frames.live_count() == 0, 'ring kept expired frames'
            assert expired >= 1, 'no expirations recorded'
            c = svc.counters()
            assert c["expired_items_deleted"] >= 1
            print(f'TEST3 FULL PASS expired={expired}', flush=True)

            # TEST 4 — memory hygiene: structured obs persists sanitized,
            # screenshots never reach memory.
            from core.orchestrator.perception_service import (
                SemanticObservation)
            marker = f"p9probe{time.time_ns() % 1000000}"
            probe = SemanticObservation(
                captured_at=time.time(), active_app="probe",
                window_title=f"p9 hygiene probe {marker}",
                summary=f"{marker} user typed password=hunter2 in dialog",
                change="low", confidence=0.9, source="poll")
            assert svc.remember_observation(probe,
                                            category="screen_state_p9test"), \
                'remember_observation failed'
            rows = memory.facts_by_category("screen_state_p9test", limit=20)
            joined = " ".join(r.get("fact", "") for r in rows)
            assert marker in joined, \
                f'probe fact not retrievable: {rows[:2]}'
            assert "hunter2" not in joined, \
                f'SECRET LEAKED TO MEMORY: {joined[:200]}'
            assert "<REDACTED" in joined, \
                f'secret not redacted: {joined[:200]}'
            low = joined.lower()
            assert "bytes" not in low and "blob" not in low \
                and "png" not in low, \
                f'pixel payload suspected in memory: {joined[:200]}'
            print('TEST4 FULL PASS fact sanitized + retrievable, no pixels',
                  flush=True)

            # TEST 5 — metrics + CPU budget: fast ticks measured, p95 bounded.
            ms = svc.metrics_summary()
            assert "fast_tick_ms" in ms and ms["fast_tick_ms"]["count"] >= 5, \
                f'no fast-tick samples: {ms}'
            p95 = ms["fast_tick_ms"]["p95"]
            assert p95 < 500.0, f'fast tick p95 {p95:.1f}ms over budget'
            print(f'TEST5 FULL PASS fast_tick p95={p95:.1f}ms '
                  f'n={int(ms["fast_tick_ms"]["count"])}', flush=True)

            spy.assert_clean()
            print('SPY CLEAN tools=%s' % sorted(set(spy.tools)), flush=True)
    finally:
        for h in list(test_hwnds):
            close_exact(h)
        time.sleep(1.0)
        try:
            svc.stop()
        except Exception:
            pass
        leftovers = windows_of('notepad.exe') - before['notepad.exe']
        assert not leftovers, f'test-owned windows left behind: {leftovers}'
        after_all = snapshot_desktop()
        for k, v in before.items():
            lost = v - after_all[k]
            assert not lost, f'PRE-EXISTING {k} WINDOWS DIED: {lost}'
        print('CLEANUP OK pre-existing alive, no leftovers', flush=True)

    print('PHASE 9 PART A: ALL 5 TESTS FULL PASS', flush=True)


if __name__ == '__main__':
    main()
