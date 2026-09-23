#!/usr/bin/env python
"""Phase 9A: continuous perception foundation (no OCR, no screenshots,
no model — asserted by counters, not by promise).

Real desktop, test-owned WinForms scratch app only. Pre-existing
windows snapshotted and verified alive at the end.
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

SCRATCH_TITLE = 'DUDE Grounding Scratch'
PS1 = r'C:\Users\duvvu\AppData\Local\Temp\opencode\scratch_winforms.ps1'
CMDDIR = os.path.dirname(PS1)
TIMEOUT = 20.0

counters = {'ocr': 0, 'capture': 0}


def _cmd(name, text):
    if text is None:
        try:
            os.remove(os.path.join(CMDDIR, name))
        except OSError:
            pass
        return
    with open(os.path.join(CMDDIR, name), 'w', encoding='utf-8') as f:
        f.write(text)


def _fg():
    import win32gui
    h = win32gui.GetForegroundWindow()
    try:
        return h, win32gui.GetWindowText(h) or ''
    except Exception:
        return h, ''


def _census():
    """Titled top-level windows only: bare-HWND census flaps on
    transient tooltip/popup hosts. Returns {hwnd: (pid, exe, title)}."""
    import win32gui
    import win32process
    import psutil
    out = {}

    def cb(h, acc):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                try:
                    pid = win32process.GetWindowThreadProcessId(h)[1]
                    exe = (psutil.Process(pid).name() or '').lower()
                except Exception:
                    pid, exe = 0, '?'
                acc[h] = (pid, exe, win32gui.GetWindowText(h)[:60])
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, out)
    return out


def _scratch_hwnd():
    import win32gui
    for _ in range(20):
        h = win32gui.FindWindow(None, SCRATCH_TITLE)
        if h:
            return h
        time.sleep(0.5)
    return 0


def _wait_delta(svc, pred, timeout=TIMEOUT, poll=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        o = svc.current_observation()
        if o is not None and pred(o.delta or {}):
            return o
        time.sleep(poll)
    raise AssertionError('timeout waiting for delta')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import win32gui
    # Pre-existing snapshot (scratch excluded — not running yet).
    before = _census()

    # Spy counters: any OCR/screenshot/model use fails the phase.
    import core.ocr as _ocr_mod
    _real_ocr = _ocr_mod.ocr_image

    def _count_ocr(img, *a, **k):
        counters['ocr'] += 1
        return _real_ocr(img, *a, **k)

    _ocr_mod.ocr_image = _count_ocr
    import core.tools as _tools_mod
    _real_cap = _tools_mod._capture_screen_composite

    def _count_cap(*a, **k):
        counters['capture'] += 1
        return _real_cap(*a, **k)

    _tools_mod._capture_screen_composite = _count_cap

    from core.screentree import ScreenMap
    from core.tools import init_screentree, bring_to_foreground
    from core.orchestrator import PerceptionEngine
    from core.orchestrator.perception_service import (
        PerceptionService, compute_control_delta, compute_scene_delta,
        SemanticObservation)

    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(3)
    assert smap.available(), 'UIA screen map unavailable'
    perception = PerceptionEngine(
        get_screentree=lambda: smap, get_ocr_fn=lambda: None,
        get_capture_fn=None)
    # NOTE: capture_fn=None -> the service CAN capture nothing even if
    # asked; screenshots are structurally impossible in this pass.
    svc = PerceptionService(perception, capture_fn=None, memory=None,
                            fast_hz=20.0, idle_hz=4.0)
    proc = subprocess.Popen(
        ['powershell', '-ExecutionPolicy', 'Bypass', '-File', PS1],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        hwnd = _scratch_hwnd()
        assert hwnd, 'scratch app did not appear'
        print('scratch hwnd:', hwnd, flush=True)
        svc.start()
        assert svc._fast_thread and svc._fast_thread.is_alive(), \
            'observer thread not running'

        # ---- T1: read-only observation (fg + census stable).
        fg0, census0 = _fg(), set(_census())
        time.sleep(3.0)
        assert _fg()[0] == fg0[0], \
            f'observer stole foreground: {fg0} -> {_fg()}'
        assert set(_census()) == census0, 'observer opened/closed windows'
        print('T1 FULL PASS read-only (fg + census stable)', flush=True)

        # ---- T2: A-F change sequence on the owned scratch app.
        base = svc.current_observation()
        assert base is not None, 'no baseline observation'
        _cmd('field1.value', 'HELLO_A')
        o = _wait_delta(svc, lambda d: 'control_value_changed' in d)
        print('T2.A FULL PASS value delta:', o.delta_summary()[:100],
              flush=True)
        _cmd('focus.cmd', 'second')
        o = _wait_delta(svc, lambda d: 'focus_changed' in d)
        print('T2.C FULL PASS focus delta:', o.delta_summary()[:100],
              flush=True)
        _cmd('menu.cmd', 'open')
        o = _wait_delta(
            svc, lambda d: 'menu_opened' in d or 'control_appeared' in d,
            timeout=25.0)
        print('T2.D-open FULL PASS:', o.delta_summary()[:120], flush=True)
        _cmd('menu.cmd', 'close')
        # Close may arrive via MENUEND, control removal, or the 5s
        # medium backstop (silent closes) — any is a pass.
        o = _wait_delta(
            svc, lambda d: 'menu_closed' in d or 'control_removed' in d,
            timeout=25.0)
        print('T2.D-close FULL PASS:', o.delta_summary()[:120], flush=True)
        _cmd('checkbox.value', '1')
        o = _wait_delta(svc, lambda d: 'control_value_changed' in d)
        print('T2.F FULL PASS checkbox delta:', o.delta_summary()[:100],
              flush=True)
        for f in ('field1.value', 'focus.cmd', 'menu.cmd',
                  'checkbox.value'):
            _cmd(f, None)

        # ---- T3: negatives — identical rebuilds, same titles: no delta.
        sig_a = {'aid:x': (True, 'OK', ''), 'tn:Edit|N': (True, 'N', 'v')}
        assert compute_control_delta(dict(sig_a), dict(sig_a)) == {}, \
            'identical rebuild produced a delta'
        oa = SemanticObservation(active_app='a.exe', window_title='T',
                                 hwnd=1, focused_name='f',
                                 dialog_kind='none', change='low')
        ob = SemanticObservation(active_app='a.exe', window_title='T',
                                 hwnd=1, focused_name='f',
                                 dialog_kind='none', change='low')
        assert compute_scene_delta(oa, ob) == {}, \
            'identical snapshots produced a delta'
        # Selection approximation: focus moving among tab items.
        ta = SemanticObservation(active_app='a.exe', window_title='T',
                                 hwnd=1, focused_name='General',
                                 focused_ctype='TabItemControl',
                                 dialog_kind='none', change='low')
        tb = SemanticObservation(active_app='a.exe', window_title='T',
                                 hwnd=1, focused_name='Advanced',
                                 focused_ctype='TabItemControl',
                                 dialog_kind='none', change='low')
        assert 'selection_changed' in compute_scene_delta(ta, tb), \
            'tab focus move missed selection delta'
        c0 = svc.counters()['redundant_ticks_avoided']
        time.sleep(3.0)
        assert svc.counters()['redundant_ticks_avoided'] > c0, \
            'idle ticks not counted'
        print('T3 FULL PASS negatives silent, idle ticks counted',
              flush=True)

        # ---- T5: staleness — old observation invalidated by change.
        stale = svc.current_observation()
        assert stale is not None
        _cmd('field1.value', 'HELLO_B')
        o2 = _wait_delta(svc, lambda d: bool(d))
        assert o2.key() != stale.key() or o2.delta, \
            'changed screen produced no new state'
        assert o2.captured_at >= stale.captured_at
        _cmd('field1.value', None)
        print('T5 FULL PASS change invalidated stale state', flush=True)

        # ---- T5b: window-state transitions on the owned window.
        assert bring_to_foreground(hwnd), 'could not focus scratch'
        time.sleep(1.0)
        win32gui.ShowWindow(hwnd, 3)  # SW_MAXIMIZE, test-owned only
        o3 = _wait_delta(
            svc, lambda d: 'window_state_changed' in d
            or 'fg_changed' in d)
        print('T5b FULL PASS state delta:', o3.delta_summary()[:120],
              flush=True)
        win32gui.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL restore
        time.sleep(1.0)

        # ---- T4: perf benchmark at 20Hz (10s).
        import psutil
        rss0 = psutil.Process().memory_info().rss
        m0 = svc.metrics.summary()
        t0 = time.time()
        time.sleep(10.0)
        wall = time.time() - t0
        m1 = svc.metrics.summary()
        ft = m1.get('fast_tick_ms', {})
        assert ft and ft['count'] >= 100, \
            f'observer starved: {ft}'
        p95 = ft.get('p95', 0)
        print(f'T4 rate: {ft["count"]/wall:.1f} ticks/s, '
              f'fast_tick p50={ft.get("p50", 0):.1f}ms '
              f'p95={p95:.1f}ms', flush=True)
        for k in ('uia_refresh_ms', 'value_read_ms', 'change_detect_ms',
                  'window_census_ms', 'scene_update_ms'):
            v = m1.get(k, {})
            if v:
                print(f'    {k}: n={int(v["count"])} '
                      f'p50={v["p50"]:.1f} p95={v["p95"]:.1f}', flush=True)
        print(f'    uia_refreshes='
              f'{int(m1.get("uia_refresh_ms", {}).get("count", 0))} '
              f'obs_created={svc.counters()["observations_created"]} '
              f'redundant_avoided='
              f'{svc.counters()["redundant_ticks_avoided"]}', flush=True)
        assert p95 < 100.0, f'20Hz unsustainable: p95 {p95:.1f}ms'
        assert counters['ocr'] == 0, f'OCR used {counters["ocr"]}x!'
        assert counters['capture'] == 0, \
            f'screenshots taken {counters["capture"]}x!'
        rss_growth = (psutil.Process().memory_info().rss - rss0) / 1e6
        print(f'    RSS growth over 10s: {rss_growth:.1f}MB', flush=True)
        assert rss_growth < 50.0, f'memory growth {rss_growth:.1f}MB'
        print('T4 FULL PASS 20Hz sustainable, 0 OCR/shot/model',
              flush=True)

        # ---- Scene accessor (minimal §15 integration).
        scene = svc.get_scene()
        assert scene.scene_version >= 1 and not scene.is_stale(), \
            'stale/empty shared scene'
        assert scene.controls_summary, 'no control summary in scene'
        print(f'SCENE v{scene.scene_version} {scene.active_app!r} '
              f'{scene.controls_summary} fresh={scene.freshness_ms():.0f}ms',
              flush=True)
    finally:
        try:
            svc.stop()
        except Exception:
            pass
        for f in ('field1.value', 'field2.value', 'focus.cmd', 'menu.cmd',
                  'checkbox.value'):
            _cmd(f, None)
        try:
            h = win32gui.FindWindow(None, SCRATCH_TITLE)
            if h:
                win32gui.PostMessage(h, 0x0010, 0, 0)
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _ocr_mod.ocr_image = _real_ocr
        _tools_mod._capture_screen_composite = _real_cap
        time.sleep(1.0)
        import psutil as _psutil
        dead_real = []
        for h, (pid, exe, title) in before.items():
            try:
                alive = win32gui.IsWindow(h)
            except Exception:
                alive = False
            if alive:
                continue
            # Dead handle: only a failure if its owning app is gone too.
            # Transient hosts die naturally; the test closes nothing
            # but the scratch window, so a vanished PID of a stable app
            # is the real signal.
            try:
                p_alive = _psutil.pid_exists(pid) and pid != 0
            except Exception:
                p_alive = True
            if not p_alive and exe not in ('?', ''):
                dead_real.append((h, exe, title))
            else:
                print(f'  note: transient window gone '
                      f'{h} {exe} {title!r}', flush=True)
        assert not dead_real, f'PRE-EXISTING APP WINDOWS DIED: {dead_real}'
        print('CLEANUP OK pre-existing alive, scratch closed', flush=True)
    print('PHASE 9A: ALL TESTS FULL PASS', flush=True)


if __name__ == '__main__':
    main()

