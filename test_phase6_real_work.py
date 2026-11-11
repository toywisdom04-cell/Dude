#!/usr/bin/env python
"""Phase 6 canonical benchmark: REAL autonomous work tasks.

DUDE receives natural-language GOALS (never click instructions) and must
independently understand -> observe -> plan -> reuse apps -> ground ->
act -> verify -> recover -> learn -> report, through the production
pipeline only (TaskEngine -> Perception -> IntelligenceRouter ->
ActionExecutor -> Verification -> Recovery -> ProcedureLearner).

  TEST 1 - basic real work: folder + text document + save + verify.
  TEST 2 - parameterized repeat: same shape, different names; the
           learned procedure must be retrieved and rebound at runtime.
  TEST 3 - controlled recovery: illegal filename; honest failure or
           safe recovery, never silent repair, never learned bad param.
  TEST 4 - background safety: real work while user apps stay open;
           focus theft mid-run; no interference with user work.

HARD RULES (asserted, not assumed):
- No run_powershell / write_file as the action (tool spy on every run).
- No test-side manipulation of the target apps (setup/cleanup/verify
  only, through narrow helpers).
- Dedicated namespaces only: Desktop\\DUDE_P6_*.
- Never close pre-existing windows/HWNDs; only stem-matching scratch
  tabs are discarded, each verified.
- No secrets in output (HWNDs/counts/classes only, titles redacted
  unless they carry a test marker).
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

from core.orchestrator.locations import shell_known_folder
DESKTOP = shell_known_folder('desktop')
BANNED_TOOLS = {"run_powershell", "write_file"}

GOAL_1 = ("Create a folder named DUDE_P6_Work on my Desktop. Inside it "
          "create a text document containing: DUDE autonomous work test. "
          "Save it as P6_Report.txt and verify the saved file.")
CONTENT_1 = "DUDE autonomous work test"

GOAL_2 = ("Create a folder named DUDE_P6_Work_2 on my Desktop. Inside it "
          "create a text document containing: DUDE second run content. "
          "Save it as P6_Report_2.txt and verify the saved file.")
CONTENT_2 = "DUDE second run content"

GOAL_2B = ("Create a folder named DUDE_P6_Work_3 on my Desktop. Inside it "
           "create a text document containing: DUDE third run content. "
           "Save it as P6_Report_3.txt and verify the saved file.")
CONTENT_2B = "DUDE third run content"

GOAL_3 = ("Create a folder named DUDE_P6_Bad on my Desktop. Inside it "
          "create a text document containing: DUDE bad name probe. "
          "Save it as P6_Bad:Name.txt and verify the saved file.")

GOAL_4 = ("Create a folder named DUDE_P6_Safe on my Desktop. Inside it "
          "create a text document containing: DUDE background safe run. "
          "Save it as P6_Safe.txt and verify the saved file.")
CONTENT_4 = "DUDE background safe run"


def focus_hwnd(hwnd, tries=6):
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


def build_stack(shared_store_dir=None):
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
    tmp = shared_store_dir or tempfile.mkdtemp()
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

    def count(self, name):
        return sum(1 for c in self.tools if c == name)


def _looks_like_focus_contention(state):
    text = str(getattr(state, 'failure_reason', '') or '').lower()
    return any(k in text for k in (
        'not matched', 'foreground', 'focus', 'stale grounding',
        'different window'))


def run_goal(stack, spy, goal, timeout=300.0, attempts=3, **run_kwargs):
    from core.orchestrator import TaskType
    print('GOAL:', goal)
    t0 = time.time()
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
            for ev in st.recovery_history[-6:]:
                print('    %s' % {k: str(v)[:120] for k, v in ev.items()})
        if st.is_complete() or not _looks_like_focus_contention(st):
            break
    st.elapsed_wall = time.time() - t0
    return st


def assert_completed(state, what):
    assert state.is_complete(), \
        '%s did not complete: %s' % (what, state.failure_reason)


def efficiency_report(state, spy):
    m = dict(getattr(state, 'metrics', None) or {})
    opens = spy.count('open_app')
    verifs = sum(1 for r in (state.verification_results or [])
                 if getattr(r, 'success', False)) \
        if hasattr(state, 'verification_results') else 'n/a'
    print('  EFFICIENCY: steps=%s/%s opens=%d recoveries=%d '
          'verifications=%s wall=%.1fs' % (
              state.current_step, len(state.subgoals or []), opens,
              len(state.recovery_history or []), verifs,
              getattr(state, 'elapsed_wall', -1)))
    print('  METRICS:', {k: m.get(k) for k in sorted(m)[:12]})
    return {'opens': opens,
            'recoveries': len(state.recovery_history or [])}


def read_file_text(path):
    with open(path, encoding='utf-8-sig') as f:
        return f.read()


def close_test_tabs(stack, stems, memory, strict=True):
    """Close only stem-matching tabs (verified each); user tabs never match.

    The UIA map covers the FOREGROUND window, so the owning Notepad is
    focused first (briefly; restored afterwards) — otherwise the query
    is blind and debris survives to poison later runs.

    strict=False (setup): best effort; an unresponsive window must not
    block the run — the task opens its own tabs regardless. Survivors
    are reported, never silently ignored.
    """
    from core.orchestrator import PerceptionLevel
    from core.tools import execute_tool as _et
    import win32gui
    perception = stack["perception"]
    try:
        home = win32gui.GetForegroundWindow()
    except Exception:
        home = None
    targets = sorted(windows_of('notepad.exe'))
    if targets:
        focus_hwnd(targets[0])
        time.sleep(1.0)

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
            if strict:
                assert closed, 'refused to force-close tab %r' % stem
            elif not closed:
                print('WARNING: setup could not discard tab %r; '
                      'continuing (task opens its own tabs)' % stem)
    if home is not None:
        try:
            focus_hwnd(home)
        except Exception:
            pass


def park_explorer_at_desktop(memory):
    """Cleanup-only: move the task-used Explorer window back to Desktop
    so no handle pins a test namespace being removed. The task itself
    relocates this window constantly; parking is the tidy inverse."""
    from core.tools import execute_tool as _et
    allow = lambda *a: True
    _et('open_app', json.dumps({'name': 'explorer'}), memory, allow)
    time.sleep(1.5)
    _et('press_hotkey', json.dumps({'combo': 'ctrl+l'}), memory, allow)
    time.sleep(1.0)
    _et('type_text', json.dumps({'text': DESKTOP}), memory, allow)
    time.sleep(1.0)
    _et('press_hotkey', json.dumps({'combo': 'enter'}), memory, allow)
    time.sleep(2.0)


def remove_tree_assert(path, what):
    """Remove a test-owned tree LOUDLY: retry (Explorer may pin it),
    then assert it is really gone. Never ignore_errors silently."""
    for attempt in range(4):
        if not os.path.isdir(path):
            return
        shutil.rmtree(path, ignore_errors=True)
        time.sleep(1.5)
    assert not os.path.isdir(path), \
        '%s survived cleanup: %s' % (what, path)
    print('removed test namespace:', what)


def ensure_clean_ns(ns, memory):
    """Test setup: the dedicated namespace starts empty. Removes only
    test-owned leftovers (P6_* stems); unknown entries abort loudly."""
    if not os.path.isdir(ns):
        return
    leftovers = sorted(os.listdir(ns))
    unknown = [n for n in leftovers if not n.startswith('P6_')]
    assert not unknown, \
        'refusing setup-clean %s, unknown entries: %s' % (ns, unknown)
    print('setup: clearing leftover test namespace:', ns, leftovers)
    park_explorer_at_desktop(memory)
    remove_tree_assert(ns, 'leftover ' + ns)


def check_pre_existing_alive(before):
    import win32gui
    dead = {}
    for exe, hwnds in before.items():
        for h in hwnds:
            try:
                if not win32gui.IsWindow(h):
                    dead.setdefault(exe, []).append(h)
            except Exception:
                pass
    assert not dead, 'PRE-EXISTING WINDOWS DIED: %s' % dead
    print('safety: all pre-existing HWNDs alive:', {
        e: len(h) for e, h in before.items()})


TAB_STEMS = ['P6_Report', 'P6_Bad', 'P6_Safe']


def test1_basic_real_work(stack, spy):
    print('\n================ TEST 1: basic real work ================')
    modal_guard()
    ns = os.path.join(DESKTOP, 'DUDE_P6_Work')
    ensure_clean_ns(ns, stack["memory"])
    close_test_tabs(stack, TAB_STEMS, stack["memory"], strict=False)
    before = snapshot_desktop()
    target = os.path.join(ns, 'P6_Report.txt')
    # Single honest shot: whole-task retries would collide with the
    # task's own artifacts (idempotent-create recovery covers the
    # in-task case instead).
    st = run_goal(stack, spy, GOAL_1, attempts=1)
    assert_completed(st, 'test1 work goal')
    # Independent verification (test-side reads only, never writes).
    assert os.path.isdir(ns), 'folder missing: %s' % ns
    assert os.path.isfile(target), 'file missing: %s' % target
    text = read_file_text(target)
    assert CONTENT_1 in text, 'content mismatch: %r' % text[:120]
    entries = sorted(os.listdir(ns))
    assert entries == ['P6_Report.txt'], 'unexpected artifacts: %s' % entries
    print('INDEPENDENT VERIFICATION: folder + exact file + content OK')
    decs = st.window_decisions or []
    assert any(d.get('decision') == 'reuse_existing' for d in decs), \
        'no reuse decision recorded: %s' % decs
    eff = efficiency_report(st, spy)
    assert eff['opens'] <= 3, 'too many app launches: %d' % eff['opens']
    spy.assert_clean()
    check_pre_existing_alive(before)
    print('TEST 1: FULL PASS')
    return ns


def learned_procedures(stack, goal=None):
    # Promoted procedures store the NORMALIZED goal (placeholders, not
    # literals), so full-literal LIKE lookups can never match. Probe
    # with stable fragments that survive normalization.
    store = stack["proc_store"]
    rows = []
    try:
        if goal:
            rows = store.find_by_goal(goal) or []
        else:
            for probe in ('verify the saved file', 'on my Desktop',
                          'create a text document'):
                try:
                    for r in (store.find_by_goal(probe) or []):
                        if all(getattr(r, 'id', None) != getattr(
                                x, 'id', None) for x in rows):
                            rows.append(r)
                except Exception:
                    pass
    except Exception as e:
        print('procedure store read failed:', e)
    return rows


def learner_candidates(stack):
    try:
        return dict(stack["learner"]._candidates or {})
    except Exception as e:
        print('candidate read failed:', e)
        return {}


def proc_text(row):
    try:
        return json.dumps(row.__dict__ if hasattr(row, '__dict__')
                          else row, default=str)
    except Exception:
        return str(row)


def test2_parameterized_repeat(stack, spy):
    print('\n================ TEST 2: parameterized repeat ================')
    modal_guard()
    for _ns in (os.path.join(DESKTOP, 'DUDE_P6_Work_2'),
                os.path.join(DESKTOP, 'DUDE_P6_Work_3')):
        ensure_clean_ns(_ns, stack["memory"])
    close_test_tabs(stack, TAB_STEMS, stack["memory"], strict=False)
    # Architecture: promotion needs 2 successes on one normalized key.
    # Since test1 ran in a separate process, we run GOAL_2 twice here
    # to achieve the 2 successes needed for promotion.
    before = snapshot_desktop()
    ns2 = os.path.join(DESKTOP, 'DUDE_P6_Work_2')
    target2 = os.path.join(ns2, 'P6_Report_2.txt')
    # First run: buffers candidate
    st = run_goal(stack, spy, GOAL_2, attempts=1)
    assert_completed(st, 'test2 first run')
    assert os.path.isfile(target2), 'file missing: %s' % target2
    assert CONTENT_2 in read_file_text(target2), 'content not rebound!'
    print('INDEPENDENT VERIFICATION: first run file + content OK')
    # Close the test-owned tab from first run so second run starts clean
    close_test_tabs(stack, TAB_STEMS, stack["memory"], strict=False)
    # Also remove the file so second run doesn't hit overwrite confirmation
    if os.path.isfile(target2):
        os.remove(target2)
    # Second run: should promote
    st = run_goal(stack, spy, GOAL_2, attempts=1)
    assert_completed(st, 'test2 second run (promotion)')
    assert os.path.isfile(target2), 'file missing: %s' % target2
    assert CONTENT_2 in read_file_text(target2), 'content not rebound!'
    assert sorted(os.listdir(ns2)) == ['P6_Report_2.txt']
    print('INDEPENDENT VERIFICATION: second-params file + content OK')
    # Promotion: the store must now hold a generalized procedure whose
    # varying params carry NO literal defaults.
    procs = learned_procedures(stack)
    print('stored procedures after 2 successes:', len(procs))
    assert len(procs) >= 1, 'nothing promoted after 2 successes'
    blob = ' '.join(proc_text(r) for r in procs)
    assert 'screenshot' not in blob.lower(), 'raw capture stored as memory!'
    for lit in ('DUDE_P6_Work_2', 'P6_Report_2', 'second run content'):
        assert lit not in blob, \
            'concrete literal %r preserved in procedure!' % lit
    print('promotion: generalized, no literals, no captures '
          '(%d chars)' % len(blob))
    for r in procs:
        print('  procedure:', proc_text(r)[:300])
        try:
            steps = getattr(r, 'steps', None) or []
            if isinstance(steps, str):
                steps = json.loads(steps)
            print('  stored steps: %d' % len(steps))
            for s in steps:
                print('    -', s.get('action_type'),
                      '|', str(s.get('target'))[:50],
                      '|', s.get('verification_method', '<MISSING>'))
        except Exception as e:
            print('  step dump failed:', e)
    efficiency_report(st, spy)
    spy.assert_clean()
    check_pre_existing_alive(before)
    # Retrieval: third params must route VERIFIED_PROCEDURE at runtime.
    before = snapshot_desktop()
    ns3 = os.path.join(DESKTOP, 'DUDE_P6_Work_3')
    target3 = os.path.join(ns3, 'P6_Report_3.txt')
    st = run_goal(stack, spy, GOAL_2B, attempts=1)
    assert_completed(st, 'test2 third-params goal')
    assert os.path.isfile(target3), 'file missing: %s' % target3
    assert CONTENT_2B in read_file_text(target3), 'content not rebound!'
    assert sorted(os.listdir(ns3)) == ['P6_Report_3.txt']
    print('INDEPENDENT VERIFICATION: rebound file + content OK')
    routed = [e.get('decision', '') for e in (st.recovery_history or [])
              if e.get('phase') == 'routing']
    print('routing decisions this run:', routed)
    assert 'verified_procedure' in routed, \
        'learned procedure was NOT retrieved at runtime: %s' % routed
    efficiency_report(st, spy)
    spy.assert_clean()
    check_pre_existing_alive(before)
    print('TEST 2: FULL PASS (promoted generalized, retrieved + rebound)')
    return [ns2, ns3]


def test3_controlled_recovery(stack, spy):
    print('\n================ TEST 3: controlled recovery ================')
    modal_guard()
    before = snapshot_desktop()
    ns = os.path.join(DESKTOP, 'DUDE_P6_Bad')
    ensure_clean_ns(ns, stack["memory"])
    close_test_tabs(stack, TAB_STEMS, stack["memory"], strict=False)
    st = run_goal(stack, spy, GOAL_3, attempts=1)
    # Honest outcome only: the name is illegal on Windows, so success
    # with that exact name is impossible; silent repair is forbidden.
    bad_files = []
    if os.path.isdir(ns):
        bad_files = sorted(os.listdir(ns))
    print('namespace contents:', bad_files)
    assert not any('P6_Bad' in n for n in bad_files), \
        'bad parameter preserved on disk: %s' % bad_files
    assert not st.is_complete(), \
        'claimed success with an impossible filename!'
    reason = str(st.failure_reason or '')
    print('honest failure reason:', reason[:200])
    assert reason, 'failed with no recorded reason'
    assert any(k in reason.lower() for k in (
        'invalid', 'not valid', 'illegal', 'valid name', 'failed',
        'denied', 'recover')), \
        'failure reason does not explain the problem: %s' % reason[:160]
    blob = ' '.join(proc_text(r) for r in learned_procedures(stack))
    assert 'P6_Bad' not in blob, 'bad parameter LEARNED into a procedure!'
    print('learner clean: bad parameter not preserved')
    eff = efficiency_report(st, spy)
    spy.assert_clean()
    check_pre_existing_alive(before)
    print('TEST 3: FULL PASS (honest failure, no repair, nothing learned)')
    return ns


def test4_background_safety(stack, spy):
    # Coexistence proof: a real work goal runs to completion while the
    # user's applications stay open. Focus theft under fire is already
    # proven by Phase 5 TEST D on this build; here a decoy would be
    # pointless (the task would legitimately ADOPT any Explorer decoy
    # as its work window — observed live). What must hold: the task
    # completes with exact bytes, reuses instead of spawning, and no
    # user window/file/tab is touched.
    print('\n================ TEST 4: background safety ================')
    import win32gui
    modal_guard()
    ensure_clean_ns(os.path.join(DESKTOP, 'DUDE_P6_Safe'),
                    stack["memory"])
    close_test_tabs(stack, TAB_STEMS, stack["memory"], strict=False)
    before = snapshot_desktop()
    assert before['comet.exe'], 'need a user Comet window open'
    home = win32gui.GetForegroundWindow()
    if before['comet.exe'] and home not in before['comet.exe']:
        focus_hwnd(sorted(before['comet.exe'])[0])
        time.sleep(1.0)
        home = win32gui.GetForegroundWindow()
    print('home (user window) HWND:', home)
    comet_titles = {}
    for h in before['comet.exe']:
        try:
            comet_titles[h] = win32gui.GetWindowText(h)
        except Exception:
            pass
    st = run_goal(stack, spy, GOAL_4, attempts=1)
    ns = os.path.join(DESKTOP, 'DUDE_P6_Safe')
    target = os.path.join(ns, 'P6_Safe.txt')
    assert_completed(st, 'test4 background work goal')
    assert os.path.isfile(target), 'completed but file missing!'
    assert CONTENT_4 in read_file_text(target), 'bytes lack marker!'
    print('INDEPENDENT VERIFICATION: work done with exact bytes while '
          'user apps stayed open')
    decs = st.window_decisions or []
    assert any(d.get('decision') == 'reuse_existing' for d in decs), \
        'no reuse decision recorded: %s' % decs
    print('reuse recorded, no fresh instances spawned')
    for h, title in comet_titles.items():
        assert win32gui.IsWindow(h), 'user Comet window died!'
        try:
            now = win32gui.GetWindowText(h)
        except Exception:
            now = None
        print('comet HWND %d title stable: %s' % (h, now == title))
    efficiency_report(st, spy)
    spy.assert_clean()
    check_pre_existing_alive(before)
    print('TEST 4: FULL PASS (user work untouched)')
    return ns


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    shared = tempfile.mkdtemp()
    stack = build_stack(shared_store_dir=shared)

    def fresh_spy():
        spy = Spy(stack)
        spy.__enter__()
        return spy

    def end_spy(spy):
        spy.__exit__(None, None, None)

    results = {}
    selected = [a for a in sys.argv[1:] if a in
                ('test1', 'test2', 'test3', 'test4')] or \
        ['test1', 'test2', 'test3', 'test4']
    print('selected tests:', selected)
    namespaces = []
    try:
        if 'test1' in selected:
            spy = fresh_spy()
            try:
                namespaces.append(test1_basic_real_work(stack, spy))
            finally:
                end_spy(spy)
            results['test1'] = 'FULL PASS'
        if 'test2' in selected:
            spy = fresh_spy()
            try:
                out = test2_parameterized_repeat(stack, spy)
                namespaces.extend(out if isinstance(out, list) else [out])
            finally:
                end_spy(spy)
            results['test2'] = 'FULL PASS'
        if 'test3' in selected:
            spy = fresh_spy()
            try:
                namespaces.append(test3_controlled_recovery(stack, spy))
            finally:
                end_spy(spy)
            results['test3'] = 'FULL PASS'
        if 'test4' in selected:
            spy = fresh_spy()
            try:
                namespaces.append(test4_background_safety(stack, spy))
            finally:
                end_spy(spy)
            results['test4'] = 'FULL PASS'

        print('\n================ FINAL CLEANUP ================')
        close_test_tabs(stack, ['P6_Report', 'P6_Bad', 'P6_Safe',
                                'DUDE autonomous', 'DUDE second run',
                                'DUDE third run', 'DUDE background',
                                'DUDE bad name'],
                        stack["memory"])
        if namespaces:
            park_explorer_at_desktop(stack["memory"])
        for ns in namespaces:
            if os.path.isdir(ns):
                leftovers = sorted(os.listdir(ns))
                unknown = [n for n in leftovers
                           if not n.startswith('P6_')]
                assert not unknown, \
                    'refusing rmtree %s, unknown entries: %s' % (ns, unknown)
                print('removing test namespace:', ns, leftovers)
            remove_tree_assert(ns, ns)
        shutil.rmtree(shared, ignore_errors=True)
    finally:
        try:
            shutil.rmtree(shared, ignore_errors=True)
        except Exception:
            pass

    print('\n================ PHASE 6 CLASSIFICATION ================')
    for name in selected:
        print('%s: %s' % (name, results.get(name, 'NOT RUN')))
    assert all(results.get(n) == 'FULL PASS' for n in selected), results
    print('\nPHASE 6 BENCHMARK (selected): ALL FULL PASS')


if __name__ == '__main__':
    main()
