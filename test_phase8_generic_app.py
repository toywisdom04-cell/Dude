#!/usr/bin/env python
"""Phase 8 canonical test: GENERIC application competence (Calculator).

Goal-only natural language. No hardcoded coordinates, no app-specific
action routines, no test-side clicks/typing. DUDE must: identify the
app, reuse/open it, ground live UIA controls by semantic name, act
through ActionExecutor, and verify the displayed result.

BANNED for the task: run_powershell, write_file, direct UI manipulation
from the test, hardcoded coordinates. The test may snapshot windows,
verify final state, and close ONLY test-owned windows (exact HWNDs).
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

GOAL = ("Open Calculator, calculate 2 + 2, and verify that the displayed "
        "result is 4.")
BANNED_TOOLS = {"run_powershell", "write_file"}


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


def calc_windows():
    found = set()
    for exe in ('calc.exe', 'calculator.exe',
                'applicationframehost.exe'):
        found |= windows_of(exe)
    return found


def snapshot_desktop():
    return {
        'notepad.exe': windows_of('notepad.exe'),
        'explorer.exe': windows_of('explorer.exe'),
        'comet.exe': windows_of('comet.exe'),
        'calc': calc_windows(),
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


def read_calc_display(hwnd):
    """Independent verification: read Calculator's display via UIA."""
    import uiautomation as auto
    texts = []

    def rec(c, d):
        if d > 8:
            return
        try:
            kids = c.GetChildren()
        except Exception:
            return
        for ch in kids:
            try:
                n = getattr(ch, 'ControlTypeName', '') or ''
                nm = (ch.Name or '').strip()
            except Exception:
                continue
            if n == 'TextControl' and nm:
                texts.append(nm)
                continue
            if n in ('PaneControl', 'GroupControl', 'CustomControl',
                     'WindowControl', 'TabControl'):
                rec(ch, d + 1)

    with auto.UIAutomationInitializerInThread():
        rec(auto.ControlFromHandle(hwnd), 0)
    return texts


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    modal_guard()
    before = snapshot_desktop()
    print('PRE-STATE calc windows:', len(before['calc']))
    stack = build_stack()
    spy = Spy(stack)
    spy.__enter__()
    try:
        from core.orchestrator import TaskType
        print('GOAL:', GOAL)
        t0 = time.time()
        import asyncio
        st = asyncio.run(asyncio.wait_for(
            stack["engine"].run(GOAL, TaskType.AUTOMATE),
            timeout=300.0))
        wall = time.time() - t0
        print('PLAN %d subgoals, FINAL engine=%s step=%s/%s failure=%s'
              % (len(st.subgoals or []), stack["engine"].state,
                 st.current_step, len(st.subgoals or []),
                 st.failure_reason))
        for i, sg in enumerate(st.subgoals or []):
            print('  subgoal %d: %s [%s]' % (
                i, sg.description, sg.action_type))
        for d in (st.window_decisions or []):
            print('  window decision:', d)
        assert st.is_complete(), \
            'calculator goal did not complete: %s' % st.failure_reason
        # Independent verification: read the live display, expect 4.
        after = calc_windows()
        assert after, 'no Calculator window exists after the run'
        target = stack["engine"].task_state.target_hwnd
        hwnds = [target] if target in after else sorted(after)
        shown = []
        for h in hwnds:
            try:
                shown.extend(read_calc_display(h))
            except Exception as e:
                print('display read failed on %d: %s' % (h, e))
        print('display texts:', shown[:8])
        assert any('4' in t for t in shown), \
            'displayed result is not 4: %r' % (shown[:8],)
        print('INDEPENDENT VERIFICATION: display shows 4')
        spy.assert_clean()
        print('EFFICIENCY: wall=%.1fs opens=%d recoveries=%d' % (
            wall, spy.tools.count('open_app'),
            len(st.recovery_history or [])))
        # Safety: pre-existing windows alive; close ONLY test-owned.
        import win32gui
        for exe, hwnds in before.items():
            for h in hwnds:
                assert win32gui.IsWindow(h), \
                    'pre-existing %s window %d died!' % (exe, h)
        print('safety: all pre-existing HWNDs alive:',
              {e: len(h) for e, h in before.items()})
        owned = sorted(set(after) - set(before['calc']))
        print('test-owned calc windows:', owned)
        for h in owned:
            from core.tools import bring_to_foreground
            from core.tools import execute_tool as _et
            assert bring_to_foreground(h), \
                'could not focus test-owned window %d' % h
            time.sleep(1.0)
            _et('press_hotkey', json.dumps({'combo': 'alt+f4'}),
                stack["memory"], lambda *a: True)
            gone = not win32gui.IsWindow(h)
            for _ in range(20):
                if gone:
                    break
                time.sleep(0.5)
                gone = not win32gui.IsWindow(h)
            assert gone, 'test-owned window %d did not close' % h
        print('TEST CALC: FULL PASS')
    finally:
        spy.__exit__(None, None, None)
        try:
            shutil.rmtree(stack["tmp"], ignore_errors=True)
        except Exception:
            pass


if __name__ == '__main__':
    main()
