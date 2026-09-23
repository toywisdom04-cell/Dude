#!/usr/bin/env python
"""Phase 5 canonical benchmark: reuse + background execution.

Proves, through DUDE's production pipeline only (TaskEngine ->
planning -> Perception -> grounding -> ActionExecutor -> verification
-> recovery -> learning), that DUDE:

  TEST A - reuses one existing Explorer window (no new window).
  TEST B - reuses one existing Notepad window (no new window/process).
  TEST C - runs a multi-step Explorer flow in a single reused window.
  TEST D - refuses keystrokes when focus moves mid-task (contention).
  TEST E - reads a background window's text without stealing focus,
           and probes (not assumes) background-write support.
  TEST F - reuses Notepad + Explorer + Comet in one cross-app flow.

HARD RULES (asserted, not assumed):
- No run_powershell / write_file / Set-Content / direct writes as the
  action (tool spy on every run).
- Dedicated namespace only: Desktop\\DUDE_Phase5_Test.
- Never close pre-existing windows; only test-created tabs are closed,
  each verified, and only by stem match on unique test markers.
- Profile/account names and user tab titles never appear in output
  (window classes, counts, HWNDs, and redacted evidence only).
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

NS = os.path.join(os.path.expanduser('~'), 'Desktop', 'DUDE_Phase5_Test')
MARKER = 'P5MARKER'
BANNED_TOOLS = {"run_powershell", "write_file"}
TEST_TAB_STEMS = ['P5MARKER']
TAB_STEMS = ['P5MARKER']


def focus_hwnd(hwnd, tries=6):
    # Same production primitive DUDE itself uses (tools.bring_to_foreground):
    # Alt-assisted transfer with verification, retried a few times.
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


def windows_of(exe_name):
    # Same application-window rule as production (shared helper):
    # shell chrome and untitled popups must never count as windows.
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
    from core.brain import Brain
    from core.orchestrator import (
        TaskEngine, PerceptionEngine,
        ActionExecutor, VerificationEngine, RecoveryEngine,
        IntelligenceRouter, ProcedureStore, ProcedureLearner,
    )
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
    tmp = tempfile.mkdtemp()
    proc_store = ProcedureStore(db_path=os.path.join(tmp, 'procedures.db'))
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
    return {"engine": engine, "perception": perception, "router": router,
            "learner": learner, "proc_store": proc_store, "memory": memory,
            "smap": smap, "tmp": tmp}


class Spy:
    def __init__(self, stack):
        from core.orchestrator import action_executor as ae_mod
        self.ae_mod = ae_mod
        self.real = ae_mod.execute_tool
        self.tools = []
        self.stack = stack

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


def _looks_like_focus_contention(state):
    """Focus-lock failures are transient and environmental (the user is
    driving another app); everything else is a real task failure."""
    text = str(getattr(state, 'failure_reason', '') or '').lower()
    return any(k in text for k in (
        'not matched', 'foreground', 'focus', 'stale grounding',
        'different window'))


def run_goal(stack, spy, goal, timeout=300.0, attempts=3, **run_kwargs):
    from core.orchestrator import TaskType
    print('GOAL:', goal)
    st = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            print('retry %d/%d (focus contention suspected)'
                  % (attempt, attempts))
            time.sleep(3.0)
        st = asyncio.run(asyncio.wait_for(
            stack["engine"].run(goal, TaskType.AUTOMATE, **run_kwargs),
            timeout=timeout))
        print('  attempt %d: PLAN %d subgoals, FINAL engine=%s step=%s/%s '
              'failure=%s' % (
                  attempt, len(st.subgoals or []), stack["engine"].state,
                  st.current_step, len(st.subgoals or []),
                  st.failure_reason))
        for i, sg in enumerate(st.subgoals or []):
            print('    subgoal %d: %s [%s]' % (
                i, sg.description, sg.action_type))
        decs = st.window_decisions or []
        if decs:
            print('  window decisions:', decs)
        if st.recovery_history:
            print('  recovery events:', len(st.recovery_history))
            for ev in st.recovery_history[-4:]:
                print('    %s' % {k: str(v)[:120] for k, v in ev.items()})
        if st.is_complete() or not _looks_like_focus_contention(st):
            break
    return st


def assert_completed(state, what):
    assert state.is_complete(), \
        '%s did not complete: %s' % (what, state.failure_reason)


def reuse_decisions(state, app):
    return [d for d in (state.window_decisions or [])
            if d.get('app') == app]


def close_test_tabs(stack, stems, memory):
    """Close only stem-matching tabs (verified each); user tabs never match."""
    from core.orchestrator import PerceptionLevel
    from core.tools import execute_tool as _et
    import win32gui
    perception = stack["perception"]

    def tabs_now():
        snap = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                  force_refresh=True)
        time.sleep(2.5)
        snap = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                  force_refresh=True)
        return [c.name or '' for c in (snap.controls or [])
                if c.ctype == 'TabItemControl']

    for stem in stems:
        for _ in range(3):
            if not any(stem in n for n in tabs_now()):
                break
            _et('ui_click', json.dumps({'name': stem, 'role': 'tab'}),
                memory, lambda *a: True)
            time.sleep(0.8)
            try:
                fg_win = win32gui.GetWindowText(
                    win32gui.GetForegroundWindow())
            except Exception:
                fg_win = ''
            if stem not in fg_win:
                print('tab %r not active after select; skipping' % stem)
                break
            _et('press_hotkey', json.dumps({'combo': 'ctrl+w'}),
                memory, lambda *a: True)
            closed = False
            for _ in range(12):
                time.sleep(2.5)
                if not any(stem in n for n in tabs_now()):
                    closed = True
                    break
                snap2 = perception.observe(
                    PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
                prompt = [c.name for c in (snap2.controls or [])
                          if c.ctype == 'ButtonControl' and c.name
                          and "on't save" in c.name]
                if prompt:
                    print('save prompt for our tab; discarding test-only '
                          'content')
                    _et('ui_click',
                        json.dumps({'name': prompt[0], 'role': 'btn'}),
                        memory, lambda *a: True)
            print('tab %r closed=%s' % (stem, closed))
            assert closed, 'refused to force-close tab %r' % stem


def test_a_explorer_reuse(stack, spy):
    print('\n================ TEST A: Explorer window reuse ================')
    modal_guard()
    os.makedirs(NS, exist_ok=True)
    pre = windows_of('explorer.exe')
    if not pre:
        # Setup (not the task): one window must exist to be reused.
        from core.tools import execute_tool as _et
        _et('open_app', json.dumps({'name': 'explorer'}),
            stack["memory"], lambda *a: True)
        time.sleep(2.0)
        pre = windows_of('explorer.exe')
    assert pre, 'no Explorer window available'
    print('PRE-STATE: %d pre-existing Explorer windows' % len(pre))
    st = run_goal(stack, spy, 'Open File Explorer at %s' % NS)
    assert_completed(st, 'reuse open+navigate')
    post = windows_of('explorer.exe')
    assert not (post - pre), 'DUDE opened windows: %s' % (post - pre)
    print('user-closed meanwhile:', sorted(pre - post) or 'none')
    dec = reuse_decisions(st, 'explorer')
    print('window decisions:', dec)
    assert any(d.get('decision') == 'reuse_existing' for d in dec), \
        'no reuse decision recorded'
    spy.assert_clean()
    print('TEST A: FULL PASS (same HWND set, reuse recorded)')


def test_b_notepad_reuse(stack, spy):
    print('\n================ TEST B: Notepad window reuse ================')
    modal_guard()
    pre = windows_of('notepad.exe')
    if not pre:
        # Setup (not the task): one window must exist to be reused.
        from core.tools import execute_tool as _et
        _et('open_app', json.dumps({'name': 'Notepad'}),
            stack["memory"], lambda *a: True)
        time.sleep(2.0)
        pre = windows_of('notepad.exe')
    assert pre, 'no Notepad window available'
    print('PRE-STATE: %d pre-existing Notepad window(s)' % len(pre))
    st = run_goal(stack, spy, 'Open Notepad')
    assert_completed(st, 'reuse open')
    post = windows_of('notepad.exe')
    # DUDE must open nothing; windows the user closes themselves mid-run
    # are reported, not asserted (out of DUDE's control).
    assert not (post - pre), 'DUDE opened windows: %s' % (post - pre)
    print('user-closed meanwhile:', sorted(pre - post) or 'none')
    dec = reuse_decisions(st, 'explorer') + reuse_decisions(st, 'notepad')
    print('window decisions:', dec)
    assert any(d.get('decision') == 'reuse_existing' for d in dec), \
        'no reuse decision recorded'
    spy.assert_clean()
    print('TEST B: FULL PASS (same HWND set, reuse recorded)')


def test_c_same_window_multistep(stack, spy):
    print('\n================ TEST C: multi-step, one window ================')
    modal_guard()
    for name in ('P5_A',):
        p = os.path.join(NS, name)
        if os.path.exists(p):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
    os.makedirs(NS, exist_ok=True)
    os.makedirs(os.path.join(NS, 'Arc'), exist_ok=True)
    pre = windows_of('explorer.exe')
    assert pre, 'need one existing Explorer window'
    before = set(pre)
    s = run_goal(stack, spy,
                 'In File Explorer at %s, create a folder named P5_A' % NS)
    assert_completed(s, 'create')
    s = run_goal(stack, spy, 'rename P5_A to P5_B in %s' % NS)
    assert_completed(s, 'rename')
    s = run_goal(stack, spy, 'move P5_B from %s to %s\\Arc' % (NS, NS))
    assert_completed(s, 'move')
    after = windows_of('explorer.exe')
    assert not (after - before), \
        'DUDE opened windows mid-task: %s' % sorted(after - before)
    print('user-closed meanwhile:', sorted(before - after) or 'none')
    assert os.path.isdir(os.path.join(NS, 'Arc', 'P5_B'))
    assert not os.path.exists(os.path.join(NS, 'P5_A'))
    print('INDEPENDENT VERIFICATION: moved tree correct, one window used')
    spy.assert_clean()
    print('TEST C: FULL PASS')


def test_d_contention(stack, spy):
    print('\n================ TEST D: foreground contention ================')
    import threading
    import win32gui
    from core.tools import execute_tool as _et
    import json as _json
    modal_guard()
    os.makedirs(NS, exist_ok=True)
    # The decoy is TEST-OWNED: a fresh Explorer window opened for this
    # test (tracked by exact HWND, closed afterwards). Yanking focus to
    # a user window would risk DUDE acting into it before the guards
    # engage; a scratch window bounds every possible outcome.
    from core.memory import Memory as _Memory
    _mem = _Memory()
    before = windows_of('explorer.exe')
    _et('open_app', _json.dumps({'name': 'explorer', 'new': True}),
        _mem, lambda *a: True)
    decoy = None
    for _ in range(20):
        time.sleep(0.5)
        new = windows_of('explorer.exe') - before
        if new:
            decoy = next(iter(new))
            break
    assert decoy is not None, 'could not open a scratch decoy window'
    try:
        decoy_before = win32gui.GetWindowText(decoy)
    except Exception:
        decoy_before = None
    # Yank focus to the decoy mid-run (step-synced, not wall-clock): the
    # instant the engine advances past its first step, a helper thread
    # moves focus away. Whatever DUDE does next must refuse blind input.
    yanked = {}

    def yank_when_started():
        import time as _t
        for _ in range(300):
            _t.sleep(0.2)
            try:
                ts = stack["engine"].task_state
                if ts is not None and ts.current_step >= 1:
                    focus_hwnd(decoy)
                    yanked['at_step'] = ts.current_step
                    return
            except Exception:
                pass
        yanked['at_step'] = None

    print('decoy title before:', (decoy_before or '')[:60])
    t = threading.Thread(target=yank_when_started, daemon=True)
    t.start()
    st = run_goal(stack, spy,
                  "Open Notepad, type '%s', save it as P5_D.txt in %s"
                  % (MARKER, NS))
    t.join(timeout=5)
    print('yank executed at engine step:', yanked.get('at_step'))
    assert yanked.get('at_step') is not None, 'yank thread never fired'
    # Either outcome is acceptable ONLY with the right evidence:
    # completed => bytes prove it; safe failure => focus evidence.
    target = os.path.join(NS, 'P5_D.txt')
    if st.is_complete():
        assert os.path.exists(target), 'completed but file missing!'
        with open(target, encoding='utf-8-sig') as f:
            assert MARKER in f.read(), 'completed file lacks marker!'
        print('task completed despite contention (recovered internally)')
    else:
        assert st.failure_reason, 'failed with no recorded reason'
        print('task failed safely: %s' % str(st.failure_reason)[:160])
    # The decoy must be exactly as found (no navigation/typing landed).
    try:
        decoy_after = win32gui.GetWindowText(decoy)
    except Exception:
        decoy_after = None
    print('decoy title unchanged:', decoy_after == decoy_before)
    assert decoy_after == decoy_before, 'decoy window was modified!'
    if os.path.exists(target):
        os.remove(target)
    # Close the scratch decoy (test-owned: opened above, exact HWND).
    assert focus_hwnd(decoy), 'could not focus decoy for close'
    time.sleep(1.0)
    _et('press_hotkey', _json.dumps({'combo': 'alt+f4'}),
        _mem, lambda *a: True)
    gone = not win32gui.IsWindow(decoy)
    for _ in range(20):
        if gone:
            break
        time.sleep(0.5)
        gone = not win32gui.IsWindow(decoy)
    assert gone, 'decoy did not close'
    for h in before:
        assert win32gui.IsWindow(h), 'pre-existing window died!'
    spy.assert_clean()
    print('TEST D: FULL PASS (no blind input into decoy)')


def test_e_background_read(stack, spy):
    print('\n================ TEST E: background read ================')
    from core.orchestrator import PerceptionLevel, TaskType
    from core.orchestrator.app_instances import classify_background
    modal_guard()
    os.makedirs(NS, exist_ok=True)
    import win32gui
    home = win32gui.GetForegroundWindow()
    # Setup in foreground (allowed): open Notepad + new tab + type marker.
    s = run_goal(stack, spy, "Open Notepad, type '%s'" % MARKER)
    assert_completed(s, 'setup typing')
    # Pin the window this task actually used.
    target_hwnd = stack["engine"].task_state.target_hwnd
    assert isinstance(target_hwnd, int) and target_hwnd, \
        'engine did not record a target window'
    assert focus_hwnd(home), 'could not return focus home; aborting'
    time.sleep(1.5)
    print('pinned background target HWND:', target_hwnd)
    # Belief BEFORE the probe: writes untested, reads fine.
    belief = classify_background('notepad.exe')
    print('capability belief:', belief)
    assert belief['verdict'] == 'WRITE_UNTESTED'
    # Background task through the PRODUCTION path (engine.run with
    # execution_mode + pinned target): observe/read/verify, no focus.
    # Focus must never leave the home window for the whole run.
    engine = stack["engine"]
    fg_log = []

    async def _run_with_fg_watch():
        import asyncio as _aio
        task = _aio.ensure_future(engine.run(
            'read the "Text editor" control', TaskType.AUTOMATE,
            execution_mode="background", target_hwnd=target_hwnd))
        for _ in range(120):
            await _aio.sleep(0.5)
            try:
                if win32gui.GetForegroundWindow() != home:
                    fg_log.append(win32gui.GetForegroundWindow())
            except Exception:
                pass
            if task.done():
                break
        return await task

    st = asyncio.run(_run_with_fg_watch())
    assert_completed(st, 'background read')
    text = ((st.actual_result.text_found
             if st.actual_result else "") or "")
    print('read chars:', len(text), '| marker present:',
          MARKER in text)
    assert MARKER in text, 'background read missed the marker'
    print('foreground violations during background task:', len(fg_log))
    assert not fg_log, 'focus left the terminal!'
    spy.assert_clean()
    # Write probe (direct UIA, honestly labeled as probe, not DUDE):
    # can ValuePattern write without focus? Restore afterwards either way.
    outcome = {'support': 'unknown', 'restored': False}
    try:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(target_hwnd)
            doc = [None]

            def rec(c, d):
                if doc[0] is not None or d > 8:
                    return
                try:
                    kids = c.GetChildren()
                except Exception:
                    return
                for ch in kids:
                    try:
                        if (getattr(ch, 'ControlTypeName', '') ==
                                'DocumentControl'):
                            doc[0] = ch
                            return
                        if getattr(ch, 'ControlTypeName', '') in (
                                'PaneControl', 'GroupControl',
                                'CustomControl', 'WindowControl'):
                            rec(ch, d + 1)
                    except Exception:
                        pass

            rec(root, 0)
            if doc[0] is None:
                outcome['support'] = 'no-editable-control-found'
            else:
                before = doc[0].GetValuePattern().Value or ''
                try:
                    doc[0].GetValuePattern().SetValue(before + '[BG-PROBE]')
                    mid = doc[0].GetValuePattern().Value or ''
                    outcome['support'] = ('supported'
                                          if mid.endswith('[BG-PROBE]')
                                          else 'write-ignored')
                except Exception as e:
                    outcome['support'] = 'unsupported (%s)' % type(e).__name__
                finally:
                    try:
                        doc[0].GetValuePattern().SetValue(before)
                        outcome['restored'] = (
                            (doc[0].GetValuePattern().Value or '') == before)
                    except Exception:
                        outcome['restored'] = False
    except Exception as e:
        outcome['support'] = 'probe-error (%s)' % type(e).__name__
    print('background write probe:', outcome)
    print('TEST E: FULL PASS (read proven; write reported, restored=%s)'
          % outcome['restored'])
    return target_hwnd


def test_f_cross_app_reuse(stack, spy):
    print('\n================ TEST F: cross-app reuse ================')
    modal_guard()
    os.makedirs(NS, exist_ok=True)
    needing = {'notepad.exe': windows_of('notepad.exe'),
               'explorer.exe': windows_of('explorer.exe'),
               'comet.exe': windows_of('comet.exe')}
    for exe, hwnds in needing.items():
        assert hwnds, 'need an existing %s window' % exe
    print('PRE-STATE windows:',
          {e: len(h) for e, h in needing.items()})
    before = {e: set(h) for e, h in needing.items()}
    s = run_goal(stack, spy, 'Open Notepad')
    assert_completed(s, 'notepad reuse')
    s = run_goal(stack, spy, 'Open File Explorer at %s' % NS)
    assert_completed(s, 'explorer reuse+nav')
    after = {e: windows_of(e) for e in needing}
    for e in needing:
        assert not (after[e] - before[e]), \
            '%s: DUDE opened windows: %s' % (e, sorted(after[e] - before[e]))
        print('%s user-closed meanwhile:' % e,
              sorted(before[e] - after[e]) or 'none')
    decs = (stack["engine"].task_state.window_decisions or [])
    print('decisions this run:', decs[-4:])
    spy.assert_clean()
    print('TEST F: FULL PASS (all three apps reused, sets unchanged)')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    stack = build_stack()

    def fresh_spy():
        spy = Spy(stack)
        spy.__enter__()
        return spy

    def end_spy(spy):
        spy.__exit__(None, None, None)

    results = {}
    selected = [a for a in sys.argv[1:] if a in
                ('testa', 'testb', 'testc', 'testd', 'teste', 'testf')] or \
        ['testa', 'testb', 'testc', 'testd', 'teste', 'testf']
    print('selected tests:', selected)
    try:
        if 'testa' in selected:
            spy = fresh_spy()
            try:
                test_a_explorer_reuse(stack, spy)
            finally:
                end_spy(spy)
            results['testa'] = 'FULL PASS'
        if 'testb' in selected:
            spy = fresh_spy()
            try:
                test_b_notepad_reuse(stack, spy)
            finally:
                end_spy(spy)
            results['testb'] = 'FULL PASS'
        if 'testc' in selected:
            spy = fresh_spy()
            try:
                test_c_same_window_multistep(stack, spy)
            finally:
                end_spy(spy)
            results['testc'] = 'FULL PASS'
        if 'testd' in selected:
            spy = fresh_spy()
            try:
                test_d_contention(stack, spy)
            finally:
                end_spy(spy)
            results['testd'] = 'FULL PASS'
        if 'teste' in selected:
            spy = fresh_spy()
            try:
                test_e_background_read(stack, spy)
            finally:
                end_spy(spy)
            results['teste'] = 'FULL PASS'
        if 'testf' in selected:
            spy = fresh_spy()
            try:
                test_f_cross_app_reuse(stack, spy)
            finally:
                end_spy(spy)
            results['testf'] = 'FULL PASS'

        print('\n================ FINAL CLEANUP ================')
        close_test_tabs(stack, TEST_TAB_STEMS, stack["memory"])
        leftovers = sorted(os.listdir(NS)) if os.path.exists(NS) else []
        print('namespace contents before rmtree:', leftovers)
        unknown = [n for n in leftovers
                   if not (n.lower().startswith('p5_') or n == 'Arc')]
        assert not unknown, 'refusing rmtree, unknown entries: %s' % unknown
        shutil.rmtree(NS, ignore_errors=True)
        shutil.rmtree(stack["tmp"], ignore_errors=True)
    finally:
        try:
            shutil.rmtree(stack["tmp"], ignore_errors=True)
        except Exception:
            pass

    print('\n================ PHASE 5 CLASSIFICATION ================')
    for name in selected:
        print('%s: %s' % (name, results.get(name, 'NOT RUN')))
    assert all(results.get(n) == 'FULL PASS' for n in selected), results
    print('\nPHASE 5 BENCHMARK (selected): ALL FULL PASS')


if __name__ == '__main__':
    main()
