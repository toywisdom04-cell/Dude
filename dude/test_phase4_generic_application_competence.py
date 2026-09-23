#!/usr/bin/env python
"""Phase 4 canonical benchmark: generic application competence.

Proves DUDE carries a high-level goal across unfamiliar applications
through the SAME production pipeline every time:

    TaskEngine -> planning -> PerceptionEngine -> grounding
    -> ActionExecutor -> verification -> recovery -> learning/recall

TEST 1 - NOTEPAD (real GUI save, twice with different parameters):
    save a marker document through the visible Save As dialog, verify
    file + contents, repeat with a second filename, prove promotion and
    recall of the generalized procedure.

TEST 2 - FILE EXPLORER (real GUI manipulation):
    create a folder, rename it, move it to another test directory -
    all through the visible Explorer window. Independent filesystem
    verification only.

TEST 3 - CONTROLLED RECOVERY (unexpected dialog):
    save with an invalid filename to summon a real error dialog, then
    prove: detection -> classification -> bounded recovery (Esc
    dismissal) -> safe failure with recorded evidence. No blind clicks.

TEST 4 - CROSS-APPLICATION (one composed task):
    save a document in Notepad, then rename it in File Explorer, in a
    single TaskEngine run whose plan spans both applications.

HARD RULES (asserted, not assumed):
- No run_powershell / write_file / Set-Content / direct Python writes
  as the action. A tool spy records every tool DUDE invokes.
- Dedicated namespace only: Desktop\\DUDE_Phase4_Test. Anything outside
  it is never created, renamed, moved, or deleted by these tests.
- Only windows/tabs the test itself opened are ever closed, each close
  verified against fresh perception.
- Profile/account names and user tab titles never appear in output.
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

NS = os.path.join(os.path.expanduser('~'), 'Desktop', 'DUDE_Phase4_Test')
ARCH = os.path.join(NS, 'Archive')
# Single alphanumeric token on purpose: tesseract splinters hyphenated
# caps text ('DUDE-' + junk), breaking contiguous-substring checks, while
# single tokens read back reliably (proven: ZEBRAQUERYMARKER reads clean).
MARKER = 'DUDEPHASE4MARKER'
BANNED_TOOLS = {"run_powershell", "write_file"}


def focus_hwnd(hwnd, tries=6):
    # Same production primitive DUDE itself uses (tools.bring_to_foreground).
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


def explorer_hwnds():
    # Same application-window rule as production (shared helper):
    # shell chrome must never count as an Explorer window.
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
            if exe != 'explorer.exe':
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


def _row_get(row, key, default=''):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default) or default


def notepad_tabs(rows):
    return sorted({(_row_get(r, 'name') or '') for r in rows
                   if _row_get(r, 'ctype') == 'TabItemControl'})


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
    """Records tool calls (delegating) and per-step grounding evidence."""

    def __init__(self, stack):
        from core.orchestrator import action_executor as ae_mod
        self.ae_mod = ae_mod
        self.real = ae_mod.execute_tool
        self.tools = []
        self.grounding = []
        self.stack = stack
        self._real_execute = stack["engine"].action_executor.execute

    def __enter__(self):
        def recording(name, args_json, mem, ask_user):
            self.tools.append(name)
            return self.real(name, args_json, mem, ask_user)

        async def recording_execute(action, perception, permission_state=None):
            res = await self._real_execute(
                action=action, perception=perception,
                permission_state=permission_state)
            self.grounding.append({
                "action": action.action_type,
                "target": action.target_description,
                "method": str(res.grounded_method),
                "coords": res.grounded_coordinates,
                "success": res.success,
            })
            return res

        self.ae_mod.execute_tool = recording
        self.stack["engine"].action_executor.execute = recording_execute
        return self

    def __exit__(self, *a):
        self.ae_mod.execute_tool = self.real
        self.stack["engine"].action_executor.execute = self._real_execute

    def assert_clean(self):
        bad = sorted({c for c in self.tools if c in BANNED_TOOLS})
        assert not bad, f'BYPASS DETECTED: task used {bad}'


def run_goal(stack, spy, goal, timeout=300.0):
    from core.orchestrator import TaskType
    from core.orchestrator import PerceptionLevel
    engine = stack["engine"]
    perception = stack["perception"]
    print('GOAL:', goal)
    pre = perception.observe(PerceptionLevel.LEVEL_1_APP_WINDOW,
                             force_refresh=True)
    print('PRE-STATE: app=%r controls=n/a (L1)' %
          ((pre.active_app or ''),))
    print('OBSERVATION: fresh L1+L2 captured by engine per state')
    base = len(spy.grounding)
    # Phase 4 validated with isolated windows; keep that contract
    # explicitly under the Phase 5 flag system (default is reuse).
    st = asyncio.run(asyncio.wait_for(
        engine.run(goal, TaskType.AUTOMATE, prefer_fresh_windows=True),
        timeout=timeout))
    print('PLAN: %d subgoals' % len(st.subgoals or []))
    for i, sg in enumerate(st.subgoals or []):
        print('  subgoal %d: %s [%s] completed=%s'
              % (i, sg.description, sg.action_type, sg.completed))
    print('ACTION/GROUNDING/TARGET per executed step:')
    for g in spy.grounding[base:]:
        print('  action=%s target=%r method=%s coords=%s success=%s'
              % (g["action"], g["target"], g["method"], g["coords"],
                 g["success"]))
    print('VERIFICATION: last=%s' %
          (st.verification_result.evidence[:160]
           if st.verification_result else None))
    if st.last_action_result is not None:
        print('LAST ACTION: success=%s error=%r' % (
            st.last_action_result.success,
            (st.last_action_result.error or '')[:200]))
    if st.recovery_history:
        print('RECOVERY (%d events):' % len(st.recovery_history))
        for ev in st.recovery_history[-6:]:
            print('  %s' % {k: (str(v)[:120]) for k, v in ev.items()
                            if k in ('phase', 'kind', 'posture', 'evidence',
                                     'reason', 'action', 'success')})
    print('FINAL STATE: engine=%s step=%s/%s failure=%s'
          % (engine.state, st.current_step, len(st.subgoals or []),
             st.failure_reason))
    return st


def assert_completed(state, what):
    assert state.is_complete(), \
        '%s did not complete: %s' % (what, state.failure_reason)


def dismiss_our_save_dialog(memory, namespace):
    """Dismiss a Save dialog IFF its filename field names our namespace.

    A failed save flow can leave its own Save As dialog open, which blocks
    every retry at the open step. Blind Escape could cancel the USER's own
    save, so ownership is proven first by reading the filename field's
    live value through UIA (read-only): only our namespace dismisses.
    Returns True when something was dismissed.
    """
    import win32gui
    import uiautomation as auto
    from core.tools import execute_tool as _et
    try:
        fg = win32gui.GetForegroundWindow()
        if win32gui.GetWindowText(fg) != 'Save as':
            return False
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(fg)
            field = [None]

            def rec(c, d):
                if field[0] is not None or d > 8:
                    return
                try:
                    kids = c.GetChildren()
                except Exception:
                    return
                for ch in kids:
                    try:
                        ct = getattr(ch, 'ControlTypeName', '')
                        if (ct == 'EditControl'
                                and 'file name' in (ch.Name or '').lower()):
                            field[0] = ch
                            return
                        if ct in ('PaneControl', 'GroupControl',
                                  'CustomControl', 'WindowControl'):
                            rec(ch, d + 1)
                    except Exception:
                        pass

            rec(root, 0)
            if field[0] is None:
                return False
            try:
                value = field[0].GetValuePattern().Value or ''
            except Exception:
                return False
        if namespace.lower() not in value.lower():
            print('save dialog is not ours; leaving it alone')
            return False
        print('dismissing OUR leftover save dialog (nothing was saved)')
        _et('press_hotkey', json.dumps({'combo': 'escape'}),
            memory, lambda *a: True)
        time.sleep(1.0)
        try:
            still = (win32gui.IsWindow(fg)
                     and win32gui.GetWindowText(fg) == 'Save as')
        except Exception:
            still = False
        return not still
    except Exception as e:
        print('dialog check failed safely: %s' % e)
        return False


def run_goal_retried(stack, spy, goal, what, cleanup_files=(),
                     namespace=None, attempts=3, sweep_tabs=False):
    """Run a goal until it completes, bounding focus-contention flakes.

    Before every attempt (after the first) the caller-named OUR files are
    removed so a retried save flow never meets its own previous output
    (which would summon an overwrite dialog), and OUR leftover Save
    dialog (proven by field content) is dismissed so it cannot block the
    open step. After every attempt our test tabs are swept (non-strict)
    so failed attempts cannot overflow the tab strip for the next one.
    Transient foreground races fail safe inside the engine; the retry
    simply tries again.
    """
    last = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            for path in cleanup_files:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            if namespace is not None:
                dismiss_our_save_dialog(stack["memory"], namespace)
            print('retry %d/%d for %s' % (attempt, attempts, what))
        last = run_goal(stack, spy, goal)
        if sweep_tabs:
            try:
                close_test_tabs(stack, TEST_TAB_STEMS, stack["memory"],
                                strict=False)
            except Exception as e:
                print('hygiene sweep issue (non-fatal): %s' % e)
        if last.is_complete():
            return last
    return last


def assert_file_content(path, marker):
    # Case-insensitive: the OS keyboard state (e.g. Caps Lock left on in
    # the session) can invert typed case; the file round-trips whatever
    # was actually typed, which is what we prove here.
    assert os.path.exists(path), 'missing file: %s' % path
    with open(path, encoding='utf-8-sig') as f:
        content = f.read()
    assert marker.lower() in content.lower(), 'marker absent in %s' % path
    print('INDEPENDENT VERIFICATION: %s exists, marker present' % path)


def ensure_caps_off():
    """Normalize Caps Lock to off (read state first, never toggle blind).

    A stray Caps Lock inverts Shift-typed capitals (observed live: the
    marker round-tripped lowercase). Test-setup hygiene only; the engine
    itself must type literally.
    """
    try:
        import ctypes
        on = bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
        if on:
            import pyautogui
            pyautogui.press('capslock')
            print('caps lock was ON; turned off for deterministic typing')
    except Exception as e:
        print('caps check skipped: %s' % e)


def all_notepad_windows():
    import win32gui
    out = []

    def cb(h, acc):
        try:
            if (win32gui.IsWindowVisible(h)
                    and win32gui.GetClassName(h) == 'Notepad'):
                acc.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, out)
    return out


TEST_TAB_STEMS = ['P4_NoteA', 'P4_NoteB', 'P4_X', 'DUDEPHASE4MARKER',
                  'DUDE-PHASE4-MARKER']


def close_test_tabs(stack, stems, memory, strict=True):
    """Close only tabs whose names match our stems, in every Notepad
    window; each close verified. Fixed strings only (our unique marker
    spellings + P4_ stems) so user tabs are never candidates. Windows
    are visited one by one because perception follows the foreground.
    With strict=False failures only warn (per-run hygiene after every
    goal, so failed attempts cannot overflow the tab strip); the final
    cleanup stays strict."""
    from core.orchestrator import PerceptionLevel
    from core.tools import execute_tool as _et
    import json as _json
    perception = stack["perception"]
    for hwnd in all_notepad_windows():
        if not focus_hwnd(hwnd):
            print('tab cleanup: cannot focus window %s; skipping' % hwnd)
            continue
        time.sleep(1.5)
        for stem in stems:
            for _ in range(3):
                snap = perception.observe(
                    PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
                time.sleep(2.5)
                snap = perception.observe(
                    PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
                names = [c.name or '' for c in (snap.controls or [])
                         if c.ctype == 'TabItemControl']
                mine = [n for n in names if stem in n]
                if not mine:
                    break
                _et('ui_click', _json.dumps({'name': stem, 'role': 'tab'}),
                    memory, lambda *a: True)
                time.sleep(0.8)
                title = perception.observe(
                    PerceptionLevel.LEVEL_1_APP_WINDOW,
                    force_refresh=True).active_window.get('title', '')
                if stem not in title:
                    print('tab %r not active after select; skipping close'
                          % stem)
                    break
                _et('press_hotkey', _json.dumps({'combo': 'ctrl+w'}),
                    memory, lambda *a: True)
                closed = False
                for _ in range(12):
                    time.sleep(2.5)
                    s2 = perception.observe(
                        PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
                    if not any(stem in (c.name or '')
                               for c in (s2.controls or [])
                               if c.ctype == 'TabItemControl'):
                        closed = True
                        break
                    prompt = [c.name for c in (s2.controls or [])
                              if c.ctype == 'ButtonControl' and c.name
                              and "on't save" in c.name]
                    if prompt:
                        print('save prompt for our tab; discarding test-only '
                              'content')
                        _et('ui_click',
                            _json.dumps({'name': prompt[0], 'role': 'btn'}),
                            memory, lambda *a: True)
                print('window %s tab %r closed=%s' % (hwnd, stem, closed))
                if strict:
                    assert closed, 'refused to force-close tab %r' % stem
                elif not closed:
                    print('non-strict hygiene: leaving tab %r' % stem)


def close_test_window(hwnd, pre_existing):
    """Close one proven test-created window; others must survive."""
    import win32gui
    from core.tools import execute_tool as _et
    import json as _json
    from core.memory import Memory
    assert hwnd not in pre_existing, 'refusing: HWND is pre-existing!'
    assert focus_hwnd(hwnd), 'could not focus test window'
    time.sleep(1.0)
    _et('press_hotkey', _json.dumps({'combo': 'alt+f4'}),
        Memory(), lambda *a: True)
    gone = not win32gui.IsWindow(hwnd)
    for _ in range(40):
        if gone:
            break
        time.sleep(0.5)
        gone = not win32gui.IsWindow(hwnd)
    assert gone, 'test window did not close'
    for h in pre_existing:
        assert win32gui.IsWindow(h), 'pre-existing window died!'
    print('test window closed; pre-existing alive:', len(pre_existing))


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


def test_1_notepad_real_save(stack, spy):
    from core.orchestrator import TaskState
    from core.orchestrator import PerceptionLevel
    print('\n================ TEST 1: NOTEPAD real GUI save ================')
    modal_guard()
    ensure_caps_off()
    os.makedirs(NS, exist_ok=True)
    for f in ('P4_NoteA.txt', 'P4_NoteB.txt'):
        p = os.path.join(NS, f)
        if os.path.exists(p):
            os.remove(p)
    g1 = ("Open Notepad, type '%s', save it as P4_NoteA.txt in %s"
          % (MARKER, NS))
    g2 = ("Open Notepad, type '%s', save it as P4_NoteB.txt in %s"
          % (MARKER, NS))
    s1 = run_goal_retried(stack, spy, g1, 'save A',
                            [os.path.join(NS, 'P4_NoteA.txt')],
                            namespace=NS, sweep_tabs=True)
    assert_completed(s1, 'save A')
    assert_file_content(os.path.join(NS, 'P4_NoteA.txt'), MARKER)
    s2 = run_goal_retried(stack, spy, g2, 'save B',
                            [os.path.join(NS, 'P4_NoteB.txt')],
                            namespace=NS, sweep_tabs=True)
    assert_completed(s2, 'save B')
    assert_file_content(os.path.join(NS, 'P4_NoteB.txt'), MARKER)
    spy.assert_clean()
    # 4E: two successes with different filenames -> promotion + recall.
    cands = getattr(stack["learner"], '_candidates', {})
    print('learner candidates:', {
        k: getattr(v, 'success_count', '?') for k, v in cands.items()})
    stats = stack["proc_store"].get_stats()
    print('store stats:', stats)
    assert stats['total_procedures'] >= 1, 'promotion did not persist'
    ts = TaskState(goal=g1)
    ts.relevant_memory = None
    fresh = stack["perception"].observe(
        PerceptionLevel.LEVEL_1_APP_WINDOW, force_refresh=True)
    rr = stack["router"].route(ts, fresh, g1)
    print('recall decision:', rr.decision, '|', rr.reason)
    assert str(rr.decision) == 'RouteDecision.VERIFIED_PROCEDURE', \
        'no procedure recall: %s' % rr.decision
    print('TEST 1: FULL PASS')
    return ['P4_NoteA', 'P4_NoteB']


def _titles(hwnds):
    import win32gui
    out = {}
    for h in hwnds:
        try:
            out[h] = win32gui.GetWindowText(h)
        except Exception:
            out[h] = ''
    return out


def reset_namespace():
    """Reset the dedicated test namespace to empty.

    Refuses unless every entry is provably test-created (P4_ prefix,
    Archive dir, or the shell's own "New folder" leftovers from a
    partial run). Anything else aborts instead of deleting.
    """
    os.makedirs(NS, exist_ok=True)
    leftovers = sorted(os.listdir(NS))
    unknown = [n for n in leftovers
               if not (n.lower().startswith('p4_') or n == 'Archive'
                       or n.startswith('New folder'))]
    assert not unknown, 'refusing reset, unknown entries: %s' % unknown
    for n in leftovers:
        p = os.path.join(NS, n)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)
    assert os.listdir(NS) == [], 'namespace reset failed'


def test_2_explorer_gui(stack, spy):
    print('\n================ TEST 2: EXPLORER real GUI ops ================')
    modal_guard()
    reset_namespace()
    # NOTE (safety lesson): no title-based orphan sweep. A title match
    # cannot prove a window is test-created (the user may legitimately
    # browse the test folder), and closing it could take their other
    # tabs with it. Orphan test windows from earlier runs are reported
    # for manual close instead; per-run windows below are tracked by
    # exact HWND identity from open to close.
    for hwnd, title in _titles(explorer_hwnds()).items():
        if 'DUDE_Phase4_Test' in title or 'exp_probe' in title:
            print('orphan test window still open (left for manual close): '
                  '%r' % title[:60])
    pre = explorer_hwnds()
    print('pre-existing Explorer windows:', len(pre))

    def run_in_fresh_window(goal, what, expect_new=1):
        """Run one GUI goal in exactly the window(s) it opened, then close.

        Identity is by HWND diff (exact), cross-checked against title
        hijack: a pre-existing window retitled to our namespace aborts
        loudly instead of being closed. Closing per run (not at the end)
        keeps the desktop clean even if a later run fails. Multi-open
        plans (move navigates twice) expect more than one window.
        """
        # Titles are snapshotted fresh per run (not once per test): a
        # user peeking at the test folder BETWEEN runs must not trip the
        # hijack check. A retitle DURING a run still aborts loudly.
        before = explorer_hwnds()
        run_titles = _titles(before)
        s = run_goal(stack, spy, goal)
        assert_completed(s, what)
        for _ in range(20):
            time.sleep(0.5)
            new = explorer_hwnds() - before
            if len(new) >= expect_new:
                break
        assert len(new) == expect_new, \
            'expected exactly %d test Explorer window(s), saw %d' % (
                expect_new, len(new))
        for h, t in _titles(explorer_hwnds()).items():
            if (h in before and 'DUDE_Phase4_Test' in (t or '')
                    and 'DUDE_Phase4_Test' not in (run_titles.get(h) or '')):
                raise AssertionError(
                    'SAFETY ABORT: pre-existing window %s retitled %r -> '
                    '%r during the run; will not touch it' % (
                        h, (run_titles.get(h) or '')[:60],
                        (t or '')[:60]))
        return s, new

    def close_run_window(hwnd):
        close_test_window(hwnd, pre)
        assert hwnd not in explorer_hwnds(), 'test window did not close'

    s, w1 = run_in_fresh_window(
        'In File Explorer at %s, create a folder named P4_FolderA' % NS,
        'mkdir')
    for hwnd in w1:
        close_run_window(hwnd)
    assert os.path.isdir(os.path.join(NS, 'P4_FolderA')), 'folder A missing'
    print('INDEPENDENT VERIFICATION: folder A exists')

    s, w2 = run_in_fresh_window(
        'rename P4_FolderA to P4_FolderB in %s' % NS, 'rename')
    assert os.path.isdir(os.path.join(NS, 'P4_FolderB')), 'folder B missing'
    assert not os.path.exists(os.path.join(NS, 'P4_FolderA')), \
        'old name still present'
    print('INDEPENDENT VERIFICATION: renamed, old name gone')
    for hwnd in w2:
        close_run_window(hwnd)

    os.makedirs(ARCH, exist_ok=True)
    s, w3 = run_in_fresh_window(
        'move P4_FolderB from %s to %s' % (NS, ARCH), 'move',
        expect_new=1)
    assert os.path.isdir(os.path.join(ARCH, 'P4_FolderB')), \
        'moved folder missing at destination'
    assert not os.path.exists(os.path.join(NS, 'P4_FolderB')), \
        'source still present after move'
    print('INDEPENDENT VERIFICATION: moved, source gone')
    spy.assert_clean()
    for hwnd in w3:
        close_run_window(hwnd)
    print('TEST 2: FULL PASS')


def test_3_unexpected_dialog(stack, spy):
    print('\n================ TEST 3: unexpected-dialog recovery ================')
    modal_guard()
    ensure_caps_off()
    os.makedirs(NS, exist_ok=True)
    before = set(os.listdir(NS))
    # Bounded attempts: early steps (new tab) can lose transient races
    # with live desktop activity; each attempt is self-cleaning (tabs
    # swept) so failures cannot accumulate into later attempts.
    s = None
    for attempt in range(1, 4):
        print('----- recovery attempt %d/3 -----' % attempt)
        if attempt > 1:
            # A previous attempt can leave its own Save dialog open,
            # which blocks every open step. Dismiss only with proven
            # ownership (filename field names our namespace).
            dismiss_our_save_dialog(stack["memory"], NS)
        # NOTE on the invalid name (hard-won): it must NOT contain a
        # slash ('/' parses as a path separator, never an invalid name)
        # and must NOT use '?*<>|' etc. either (the Save dialog swallows
        # those keystrokes outright, so no error can appear). A reserved
        # device name (AUX) types normally but ALWAYS fails validation
        # with a modal error dialog. That is the honest trigger.
        s = run_goal(stack, spy,
                     "Open Notepad, type '%s', save it as AUX.txt in %s"
                     % (MARKER, NS))
        try:
            close_test_tabs(stack, TEST_TAB_STEMS, stack["memory"],
                            strict=False)
        except Exception as e:
            print('hygiene sweep issue (non-fatal): %s' % e)
        kinds = [e.get('kind') for e in s.recovery_history
                 if e.get('phase') == 'dialog_classification']
        if 'error_message' in kinds:
            break
        print('no error dialog classified yet; retrying')
    # The invalid name MUST fail the task: there is no safe success here.
    assert not s.is_complete(), 'invalid-name save must not complete'
    kinds = [e.get('kind') for e in s.recovery_history
             if e.get('phase') == 'dialog_classification']
    print('dialog classifications seen:', kinds)
    assert 'error_message' in kinds, \
        'error dialog was never classified: %s' % kinds
    dismissals = [e for e in s.recovery_history
                  if e.get('phase') == 'dialog_dismiss']
    print('dismiss attempts:', len(dismissals))
    assert dismissals and all(e.get('success') for e in dismissals), \
        'no successful Esc dismissal recorded'
    after = set(os.listdir(NS))
    assert after == before, 'stray files created: %s' % (after - before)
    assert s.failure_reason, 'no failure recorded'
    print('failure recorded:', str(s.failure_reason)[:160])
    print('failure history entries:',
          len(getattr(s, 'failure_history', []) or []))
    spy.assert_clean()
    print('TEST 3: FULL PASS (safe failure with recovery evidence)')
    # The typed marker sits in an untitled tab titled by its first line.
    return [MARKER]


def test_4_cross_application(stack, spy):
    print('\n================ TEST 4: cross-application task ================')
    modal_guard()
    ensure_caps_off()
    os.makedirs(NS, exist_ok=True)
    target = os.path.join(NS, 'P4_X.txt')
    if os.path.exists(target):
        os.remove(target)
    pre_exp = explorer_hwnds()
    goal = ("Open Notepad, type '%s', save it as P4_X.txt in %s, then "
            "in File Explorer at %s, rename P4_X.txt to P4_X_Renamed in %s"
            % (MARKER, NS, NS, NS))
    final = os.path.join(NS, 'P4_X_Renamed.txt')
    s = run_goal_retried(
        stack, spy, goal, 'cross-app save+rename',
        [target, final], namespace=NS, sweep_tabs=True)
    assert_file_content(final, MARKER)
    assert not os.path.exists(target), 'old name still present'
    print('INDEPENDENT VERIFICATION: renamed across apps, old name gone')
    spy.assert_clean()
    new = explorer_hwnds() - pre_exp
    assert len(new) == 1, \
        'expected exactly 1 test Explorer window, saw %d' % len(new)
    close_test_window(next(iter(new)), pre_exp)
    print('TEST 4: FULL PASS')
    # After the on-disk rename the open tab still shows the old name.
    return ['P4_X']


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    stack = build_stack()
    spy_holder = {}

    def fresh_spy():
        spy = Spy(stack)
        spy.__enter__()
        return spy

    def end_spy(spy):
        spy.__exit__(None, None, None)

    results = {}
    # Subset selection, e.g. `python test_phase4_...py test2`, so the
    # Explorer tests can run while Notepad is busy with user work.
    selected = [a for a in sys.argv[1:] if a in
                ('test1', 'test2', 'test3', 'test4')] or \
        ['test1', 'test2', 'test3', 'test4']
    print('selected tests:', selected)
    stems_a, stems_c, stems_d = [], [], []
    try:
        from core.orchestrator import PerceptionLevel
        base_tabs = notepad_tabs(
            (stack["perception"].observe(
                PerceptionLevel.LEVEL_2_UIA_TREE,
                force_refresh=True).controls or []))
        print('pre-existing Notepad tabs:', len(base_tabs))

        if 'test1' in selected:
            spy = fresh_spy()
            try:
                stems_a = test_1_notepad_real_save(stack, spy)
            finally:
                end_spy(spy)
            results['test1'] = 'FULL PASS'

        if 'test2' in selected:
            spy = fresh_spy()
            try:
                test_2_explorer_gui(stack, spy)
            finally:
                end_spy(spy)
            results['test2'] = 'FULL PASS'

        if 'test3' in selected:
            spy = fresh_spy()
            try:
                stems_c = test_3_unexpected_dialog(stack, spy)
            finally:
                end_spy(spy)
            results['test3'] = 'FULL PASS'

        if 'test4' in selected:
            spy = fresh_spy()
            try:
                stems_d = test_4_cross_application(stack, spy)
            finally:
                end_spy(spy)
            results['test4'] = 'FULL PASS'

        # ---- final cleanup: only test artifacts ----
        print('\n================ FINAL CLEANUP ================')
        from core.tools import execute_tool as _et
        dismiss_our_save_dialog(stack["memory"], NS)
        close_test_tabs(stack, TEST_TAB_STEMS, stack["memory"])
        leftovers = sorted(os.listdir(NS))
        print('namespace contents before rmtree:', leftovers)
        # Every artifact these tests can create starts with P4_ (or is the
        # Archive dir they were told to use). Anything else aborts removal.
        unknown = [n for n in leftovers
                   if not (n.startswith('P4_') or n == 'Archive')]
        assert not unknown, 'refusing rmtree, unknown entries: %s' % unknown
        shutil.rmtree(NS, ignore_errors=True)
        cur = stack["perception"].observe(
            PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
        missing = [t for t in base_tabs
                   if t not in notepad_tabs(cur.controls or [])]
        print('pre-existing tabs still present:',
              len(base_tabs) - len(missing), '/', len(base_tabs))
        assert not missing, 'user tabs disappeared: %s' % missing[:5]
        shutil.rmtree(stack["tmp"], ignore_errors=True)
    finally:
        try:
            shutil.rmtree(stack["tmp"], ignore_errors=True)
        except Exception:
            pass

    print('\n================ PHASE 4 BENCHMARK CLASSIFICATION ================')
    for name in selected:
        print('%s: %s' % (name, results.get(name, 'NOT RUN')))
    assert all(results.get(n) == 'FULL PASS' for n in selected), results
    print('\nPHASE 4 BENCHMARK (selected): ALL FULL PASS')


if __name__ == '__main__':
    main()
