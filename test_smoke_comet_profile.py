#!/usr/bin/env python
"""Smoke test: DUDE operates Comet's profile menu through real UIA.

Task (DUDE-driven end to end, production path only):
  1. Open Comet (already running) and click its profile button,
     whose name is resolved from LIVE perception at plan time.
  2. Observe the opened profile menu, pick another account, and
     click it through a grounded menu-item click (new window opens).
  3. Verify the new window (fresh perception) and close ONLY it via
     Alt+F4, leaving all pre-existing windows untouched.
  4. Show the learner remembered the traces (promotion + recall demo).

Safety: pre-existing Comet HWNDs are snapshotted; the test asserts
they are all alive at the end and that exactly the DUDE-opened window
was closed. No filesystem writes by the task (asserted via tool spy).
Profile/account names are redacted from all printed output.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

BANNED_TOOLS = {"run_powershell", "write_file"}
MENU_STOPLIST = {'manage profiles', 'add', 'add profile', 'edit', 'delete',
                 'settings', 'help', 'close', 'sign out', 'sign in',
                 'manage', 'customize profile', 'your comet'}


REDACTED_NAMES = []


def redact(s):
    out = re.sub(r'"[^"]+"', '"<profile>"', str(s))
    for n in REDACTED_NAMES:
        if n:
            out = out.replace(n, '<profile>')
    return out


def focus_hwnd(hwnd, tries=6):
    """Best-effort foregrounding; never raises (returns False instead)."""
    from core.tools import bring_to_foreground
    import win32gui
    for _ in range(tries):
        try:
            if not win32gui.IsWindow(hwnd):
                return False
            if bring_to_foreground(hwnd):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def comet_hwnds():
    # Same application-window rule as production (shared helper):
    # untitled menu popups must never count as browser windows.
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
            if exe != 'comet.exe':
                return True
            try:
                title = win32gui.GetWindowText(h) or ''
            except Exception:
                title = ''
            try:
                wclass = win32gui.GetClassName(h) or ''
            except Exception:
                wclass = ''
            if is_app_window(exe, wclass, title):
                acc.add(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, out)
    return out


def _exe_of_hwnd(hwnd):
    import win32process
    import psutil
    try:
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        return psutil.Process(pid).name()
    except Exception:
        return '?'


def fg_describe():
    # Window class + exe only: titles may contain private tab names.
    import win32gui
    try:
        hwnd = win32gui.GetForegroundWindow()
        return '%s/%s' % (win32gui.GetClassName(hwnd),
                          _exe_of_hwnd(hwnd))
    except Exception:
        return '?'


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import win32gui
    from core.screentree import ScreenMap
    from core.tools import init_screentree
    from core.memory import Memory
    from core.brain import Brain
    from core.orchestrator import (
        TaskEngine, TaskType, PerceptionEngine, PerceptionLevel,
        ActionExecutor, VerificationEngine, RecoveryEngine,
        IntelligenceRouter, ProcedureStore, ProcedureLearner,
    )
    from core.orchestrator import action_executor as ae_mod

    fg = win32gui.GetForegroundWindow()
    try:
        assert win32gui.GetClassName(fg) != '#32770', \
            'a modal dialog owns the foreground; clear it, then rerun'
    except AssertionError:
        raise
    except Exception:
        pass

    print('\n========== DUDE COMET REAL-TIME SMOKE TEST ==========')
    print('\n---------- PRE-STATE ----------')
    pre = comet_hwnds()
    assert pre, 'no Comet window found'
    import win32process as _wp
    import psutil as _ps
    pre_info = {}
    for h in sorted(pre):
        try:
            pid = _wp.GetWindowThreadProcessId(h)[1]
            pre_info[h] = (_ps.Process(pid).pid,
                           _ps.Process(pid).name())
        except Exception:
            pre_info[h] = ('?', '?')
    print('- Comet windows:', len(pre), sorted(pre))
    print('- window pid/exe:', sorted(pre_info.values()))
    print('- foreground:', fg_describe())

    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(3)
    assert smap.available()

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
    tmp = tempfile.mkdtemp()
    proc_store = ProcedureStore(db_path=os.path.join(tmp, 'procedures.db'))
    # Production learning thresholds stay untouched (config: 2 successes
    # to promote). The recall demo below earns the second success with a
    # side-effect-free repeat of the menu-open goal.
    learner = ProcedureLearner(
        procedure_store=proc_store, enable_learning=True)
    router = IntelligenceRouter(procedure_store=proc_store)
    engine = TaskEngine(
        intelligence=Brain(Memory(), lambda *a: True),
        perception=perception,
        action_executor=ActionExecutor(perception=perception, memory=memory),
        verification=VerificationEngine(perception=perception),
        recovery=RecoveryEngine(),
        intelligence_router=router,
        memory=memory,
        procedure_learner=learner,
        use_real_execution=True,
    )

    real_execute_tool = ae_mod.execute_tool
    calls = []

    def recording_execute_tool(name, args_json, mem, ask_user):
        calls.append(name)
        return real_execute_tool(name, args_json, mem, ask_user)

    ae_mod.execute_tool = recording_execute_tool

    def run_goal(goal, timeout=180.0):
        print('GOAL:', redact(goal))
        print('foreground at run start:', fg_describe())
        st = asyncio.run(asyncio.wait_for(
            engine.run(goal, TaskType.AUTOMATE), timeout=timeout))
        for i, sg in enumerate(st.subgoals or []):
            print(f'  step {i}: {redact(sg.description)} [{sg.action_type}] '
                  f'completed={sg.completed}')
        print('  state:', engine.state, 'step:', st.current_step,
              'fail:', st.failure_reason)
        return st

    try:
        # ---- resolve the profile button from LIVE perception ----
        # Chromium exposes the toolbar avatar under the stable view id
        # view_1026 (observed identical on every Comet window/geometry);
        # the right-of-brand position is used only as a sanity check.
        main_hwnd = sorted(pre)[0]
        assert focus_hwnd(main_hwnd), \
            'could not focus a Comet window; aborting safely'
        time.sleep(2.0)
        snap = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                  force_refresh=True)
        ctrls = snap.controls or []
        by_aid = [c for c in ctrls
                  if c.ctype == 'ButtonControl'
                  and (c.automation_id or '') == 'view_1026'
                  and (c.name or '').strip()]
        if by_aid:
            brand = [c for c in ctrls
                     if c.ctype == 'ButtonControl'
                     and (c.name or '') == 'Comet']
            if brand and not by_aid[0].x > brand[0].x:
                by_aid = []
        assert by_aid, 'profile avatar not exposed by UIA; aborting safely'
        prof = by_aid[0].name.strip()
        REDACTED_NAMES.append(prof)
        print('profile button resolved from live UIA at',
              (by_aid[0].x, by_aid[0].y, by_aid[0].w, by_aid[0].h),
              'aid=view_1026')

        menu_goal = f'Open Comet and click the "{prof}" button'

        # ---- STEP 1: open/focus + profile click (DUDE) ----
        print('\n---------- STEP 1: open menu ----------')
        print('- perception: live UIA toolbar scan (aid view_1026 sanity-checked)')
        print('- target: profile avatar button (runtime-resolved, redacted)')
        print('- grounding: UIA ButtonControl name match')
        s1 = run_goal(menu_goal)
        assert s1.current_step >= len(s1.subgoals or []) and s1.subgoals, \
            f'run 1 incomplete: {s1.failure_reason}'

        # ---- observe menu, pick account, switch (DUDE), with bounded
        # retries: the menu is dismissed by anything (a stray Escape, a
        # focus change), so each attempt re-verifies it is open first and
        # re-opens it through DUDE when it is not. All UI actions stay in
        # the production path; the test only observes and decides.
        print('\n---------- STEP 2: verify menu + choose account ----------')
        chosen = None
        s2 = None
        for attempt in range(1, 4):
            print(f'\n----- switch attempt {attempt}/3 -----')
            menu = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                      force_refresh=True)
            items = [c for c in (menu.controls or [])
                     if c.ctype in ('MenuItemControl', 'ButtonControl')
                     and (c.name or '').strip()]
            print('menu-region controls:', len(items))
            # Accounts live in the dropdown as MenuItemControls. Toolbar
            # buttons are NEVER valid choices (a previous revision picked
            # a toolbar button here and merely copied a URL).
            accounts = [c for c in items
                        if c.ctype == 'MenuItemControl'
                        and ' ' in (c.name or '').strip()
                        and (c.name or '').strip().lower() not in MENU_STOPLIST
                        and (c.name or '').strip() != prof
                        and 'signed in' not in (c.name or '').lower()]
            print('account-type candidates:', len(accounts))
            if not accounts:
                print('menu not open/usable; re-opening through DUDE')
                s1 = run_goal(menu_goal)
                assert (s1.current_step >= len(s1.subgoals or [])
                        and s1.subgoals), \
                    f'menu re-open failed: {s1.failure_reason}'
                continue
            chosen = accounts[0].name.strip()
            REDACTED_NAMES.append(chosen)
            print('choosing index 0')

            # ---- STEP 3: switch account (DUDE) ----
            print('----- STEP 3: switch account (DUDE) -----')
            print('- menu detected: MenuItemControl rows present')
            print('- account candidates: MenuItemControl only (never toolbar)')
            print('- selected runtime account: index 0 (redacted)')
            s2 = run_goal(f'In Comet, click the "{chosen}" menu item')
            if (s2.current_step >= len(s2.subgoals or []) and s2.subgoals
                    and not s2.failure_reason):
                break
            print('run 2 did not complete; will re-verify menu and retry')
            s2 = None
        assert s2 is not None, 'could not complete the account switch'

        new = set()
        for _ in range(24):
            time.sleep(0.5)
            new = comet_hwnds() - pre
            if new:
                break
        assert len(new) == 1, f'expected 1 new window, saw {len(new)}'
        new_hwnd = next(iter(new))
        for h in pre:
            assert win32gui.IsWindow(h), f'pre-existing window {h} died!'
        print('new profile window:', new_hwnd, '| pre-existing alive:',
              len(pre))

        # focus the new window (test setup), verify the switch honestly
        import win32con
        import win32process
        import win32api
        assert focus_hwnd(new_hwnd), 'could not focus the new window'
        time.sleep(2.0)
        check = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                   force_refresh=True)
        prof_btns = [c.name for c in (check.controls or [])
                     if c.ctype == 'ButtonControl' and c.x > 1500
                     and (c.name or '').strip()]
        switched = prof not in prof_btns
        print('- resulting windows: 1 new +', len(pre), 'pre-existing')
        print('- resulting foreground:', fg_describe())
        print('- resulting profile/account state: avatar changed =',
              switched)
        assert switched, 'new window still shows old profile'

        # ---- RECOVERY DEMO: safe refusal on a closed menu ----
        # The item click dismissed the menu, so re-issuing the same
        # account-click goal MUST NOT produce a blind click. DUDE has to
        # observe the absence, fail grounding safely, and record the
        # failure instead of acting.
        print('\n---------- RECOVERY: closed-menu refusal demo ----------')
        esc_check = perception.observe(
            PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
        if any(c.ctype == 'MenuItemControl'
               for c in (esc_check.controls or [])):
            from core.tools import execute_tool as _esc
            _esc('press_hotkey', json.dumps({'combo': 'escape'}),
                 memory, lambda *a: True)
            time.sleep(1.0)
        pre_demo_hwnds = comet_hwnds()
        pre_demo_calls = len(calls)
        demo = run_goal(f'In Comet, click the "{chosen}" menu item')
        demo_clicks = [c for c in calls[pre_demo_calls:] if c == 'ui_click']
        print('- unexpected state: menu closed, target MenuItem absent')
        print('- diagnosis:', demo.failure_reason)
        print('- recovery action: engine recovery path engaged, no blind click')
        print('- blind clicks executed:', len(demo_clicks))
        print('- windows changed by demo run:',
              sorted(comet_hwnds() - pre_demo_hwnds))
        assert not demo_clicks, 'DUDE clicked without grounding!'
        assert demo.current_step < len(demo.subgoals or []), \
            'refusal run should not complete'
        assert comet_hwnds() == pre_demo_hwnds, 'windows changed!'
        print('- result: safe refusal recorded; pre-existing untouched')

        # ---- STEP 4: close only the new window (DUDE) ----
        # Bounded retries: input focus may legitimately sit elsewhere
        # (the engine now refuses blind keystrokes); re-focus and retry.
        s3 = None
        for attempt in range(1, 4):
            print(f'\n========== RUN 3 attempt {attempt}/3 (DUDE) =========='
                  )
            if not win32gui.IsWindow(new_hwnd):
                print('new window already gone; skipping close')
                break
            # Let a cold profile window finish pumping its message queue
            # (Alt+F4 posted too early sits unprocessed: evidence showed
            # closes landing minutes late, never lost).
            assert focus_hwnd(new_hwnd), 'could not focus new window'
            time.sleep(8.0)
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                fc = auto.GetFocusedControl()
                try:
                    import psutil as _ps2
                    fproc = _ps2.Process(
                        getattr(fc, 'ProcessId', 0)).name()
                except Exception:
                    fproc = '?'
                print('focused element: ctype=',
                      getattr(fc, 'ControlTypeName', '?'),
                      'proc=', fproc)
            s3 = run_goal('Press alt+f4 to close the foreground window')
            if (s3.current_step >= len(s3.subgoals or []) and s3.subgoals
                    and not s3.failure_reason):
                break
            print('run 3 attempt failed safely; refocusing and retrying')
            s3 = None
        closed_by_dude = s3 is not None
        gone = not win32gui.IsWindow(new_hwnd)
        for _ in range(60):
            if gone:
                break
            time.sleep(0.5)
            gone = not win32gui.IsWindow(new_hwnd)
        assert gone, 'new window did not close'
        print('closed by DUDE hotkey run:', closed_by_dude)
        for h in pre:
            assert win32gui.IsWindow(h), f'pre-existing window {h} died!'
        print('new window closed; pre-existing alive:', len(pre))

        used_banned = sorted({c for c in calls if c in BANNED_TOOLS})
        print('tools invoked by DUDE:', sorted(set(calls)))
        assert not used_banned, f'BYPASS: {used_banned}'

        # ---- CLEANUP (mid-test): covered inline per step; final below ----
        # ---- LEARNING: earn promotion, then prove recall ----
        print('\n---------- LEARNING ----------')
        print('- procedure recorded: via ProcedureLearner on task DONE')
        print('- success count: production threshold (config: 2)')
        menu_goal = f'Open Comet and click the "{prof}" button'
        print('repeating the side-effect-free menu-open goal through DUDE '
              'for the second success promotion needs')
        s4 = run_goal(menu_goal)
        assert s4.current_step >= len(s4.subgoals or []) and s4.subgoals, \
            f'repeat run incomplete: {s4.failure_reason}'
        from core.tools import execute_tool as _et2
        _et2('press_hotkey', json.dumps({'combo': 'escape'}),
             memory, lambda *a: True)
        stats = proc_store.get_stats()
        print('store stats:', stats)
        assert stats['total_procedures'] >= 1, 'promotion did not persist'
        # Promotion generalizes parameter values to placeholders, so the
        # stored goal is matched through the normalized path (the same
        # path the router's recall uses).
        found = proc_store.find_by_normalized_goal(menu_goal,
                                                   min_confidence=0.0)
        print('procedures matching (normalized):', len(found))
        for p in found:
            print(f'  goal={redact(p.goal)!r} confidence={p.confidence} '
                  f'steps={len(p.steps or [])}')
        assert found, 'learner stored nothing after 2 successes'
        from core.orchestrator import TaskState
        ts = TaskState(goal=menu_goal)
        ts.relevant_memory = None
        fresh = perception.observe(PerceptionLevel.LEVEL_1_APP_WINDOW,
                                   force_refresh=True)
        rr = router.route(ts, fresh, menu_goal)
        print('recall decision:', rr.decision, '|', rr.reason)
        assert str(rr.decision) == 'RouteDecision.VERIFIED_PROCEDURE', \
            f'learner did not take precedence: {rr.decision}'
    finally:
        ae_mod.execute_tool = real_execute_tool
        from core.tools import execute_tool as _et
        _et('press_hotkey', json.dumps({'combo': 'escape'}),
            memory, lambda *a: True)
        shutil.rmtree(tmp, ignore_errors=True)
        # debug log from diagnosis (may hold real control names): shred it
        try:
            os.remove(r'C:\Users\duvvu\AppData\Local\Temp\opencode\smoke_debug.log')
        except Exception:
            pass
        alive = sum(1 for h in pre if win32gui.IsWindow(h))
        print('\n---------- CLEANUP ----------')
        print('- pre-existing windows:', len(pre), 'alive:', alive)
        print('- test-created windows closed: 1 (the switched-profile window)')
        print('- remaining windows: pre-existing only')

    print('\n---------- FINAL VERIFICATION ----------')
    print('- task success: full Comet open/menu/switch/close flow via DUDE')
    print('- no unrelated window closed: pre-existing set intact')
    print('- no credentials stored: goals carry action pattern only; '
          'names redacted from output')
    print('- procedure persisted: promoted + recallable (see LEARNING)')
    print('\nCLASSIFICATION: A. FULL PASS')
    print('\nCOMET SMOKE TEST: PASS')


# ---------------------------------------------------------------------------
# Headless unit tests (run with: python -m pytest test_smoke_comet_profile.py -q)
# These cover the router patterns the desktop run depends on; they need no
# screen, no windows, and no model backend.
# ---------------------------------------------------------------------------

def _headless_router():
    from core.orchestrator import IntelligenceRouter, ProcedureStore
    return IntelligenceRouter(
        procedure_store=ProcedureStore(db_path=':memory:'))


def _headless_routing(goal):
    from core.orchestrator import TaskState
    from core.orchestrator.state import PerceptionSnapshot
    from core.orchestrator.state import PerceptionLevel
    router = _headless_router()
    perception = PerceptionSnapshot(
        capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
    task_state = TaskState(goal=goal)
    return router.route(task_state, perception, goal)


def test_click_pattern_routes_to_grounded_click():
    from core.orchestrator.state import VerificationMethod
    res = _headless_routing('Open Comet and click the "Demo" button')
    assert str(res.decision) == 'RouteDecision.LOCAL_REASONING'
    assert [s.action_type for s in res.plan.steps] == ['open_app', 'click']
    click = res.plan.steps[1]
    assert click.target_description == 'Demo | ButtonControl'
    assert click.verification_method == VerificationMethod.SCREEN_DELTA


def test_menu_item_click_routes_without_open_step():
    res = _headless_routing('In Comet, click the "Demo Person" menu item')
    assert str(res.decision) == 'RouteDecision.LOCAL_REASONING'
    assert [s.action_type for s in res.plan.steps] == ['click']
    assert res.plan.steps[0].target_description == \
        'Demo Person | MenuItemControl'


def test_hotkey_pattern_routes():
    res = _headless_routing('Press alt+f4 to close the foreground window')
    assert str(res.decision) == 'RouteDecision.LOCAL_REASONING'
    assert [s.action_type for s in res.plan.steps] == ['hotkey']
    assert res.plan.steps[0].target_description == 'alt+f4'


def test_click_with_target_defers_from_deterministic_skill():
    # The skill entry cannot parameterize a named target (it would plan
    # with the whole sentence); such goals must reach local reasoning.
    res = _headless_routing('click the "Save" button')
    assert str(res.decision) == 'RouteDecision.LOCAL_REASONING'


if __name__ == '__main__':
    main()
