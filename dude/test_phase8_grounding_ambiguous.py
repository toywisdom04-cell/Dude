#!/usr/bin/env python
"""Phase 8 final grounding check: ambiguous same-type controls.

Unit-deterministic ranker tests (fabricated rows mirroring a real UIA
dump) run always. Live engine E2E on a test-owned scratch window runs
ONLY when the machine is idle; otherwise it SKIPs honestly instead of
fighting the user for the foreground and proving nothing.

BANNED for tasks: run_powershell, write_file, hardcoded coordinates,
test-side clicks/typing, mocked executor, fake perception/verification.
"""
import os
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

BANNED_TOOLS = {"run_powershell", "write_file"}
IDLE_GATE_SECONDS = 120.0


def scratch_rows():
    """Fabricated rows mirroring the real WinForms dump (names, types,
    rects, order) — deterministic, no desktop needed."""
    from core.orchestrator.disambiguation import RankInput
    return [
        RankInput(name='First field:', ctype='TextControl', x=436, y=482,
                  w=114, h=42, ref='label1'),
        RankInput(name='First field:', ctype='EditControl', x=648, y=482,
                  w=280, h=40, ref='edit1'),
        RankInput(name='Second field:', ctype='TextControl', x=436, y=552,
                  w=140, h=42, ref='label2'),
        RankInput(name='Second field:', ctype='EditControl', x=648, y=552,
                  w=280, h=40, ref='edit2'),
        RankInput(name='Alpha', ctype='ButtonControl', x=648, y=638,
                  w=75, h=46, ref='alpha'),
        RankInput(name='Beta', ctype='ButtonControl', x=760, y=638,
                  w=75, h=46, ref='beta'),
        RankInput(name='Minimize', ctype='ButtonControl', x=978, y=402,
                  w=94, h=60, ref='min'),
    ]


def test_unit_first_second_fields():
    from core.orchestrator.disambiguation import select_control
    rows = scratch_rows()
    pick, ev = select_control(rows, 'first text field')
    assert pick is not None and pick.ref == 'edit1', (pick, ev)
    assert ev.get('ordinal') == 0 and ev.get('ordinal_of') == 2, ev
    print('UNIT first -> edit1', ev, flush=True)
    pick, ev = select_control(rows, 'second text field')
    assert pick is not None and pick.ref == 'edit2', (pick, ev)
    assert ev.get('ordinal') == 1, ev
    print('UNIT second -> edit2', ev, flush=True)


def test_unit_buttons_generic():
    """Same machinery, different control type — no per-type code."""
    from core.orchestrator.disambiguation import select_control
    rows = scratch_rows()
    pick, ev = select_control(rows, 'second button')
    assert pick is not None and pick.ref == 'beta', (pick, ev)
    pick, ev = select_control(rows, 'first button')
    assert pick is not None and pick.ref == 'alpha', (pick, ev)
    print('UNIT buttons alpha/beta via same ranker', flush=True)


def test_unit_label_association():
    from core.orchestrator.disambiguation import select_control, RankInput
    rows = scratch_rows()
    # Label text lives NEARBY, not in the Edit name: association must
    # still resolve to the control, never to the label itself.
    rows[1].nearby_text = 'First field:'
    rows[3].nearby_text = 'Second field:'
    pick, ev = select_control(rows, 'the field under Second field')
    assert pick is not None and pick.ref == 'edit2', (pick, ev)
    assert pick.ctype == 'EditControl', 'resolved to the label, not control!'
    print('UNIT label association -> edit2', ev, flush=True)
    # Pure label path (no ordinal words anywhere): exercised via
    # Name:/Email: labels so only label_match scoring can win.
    named = [
        RankInput(name='', ctype='EditControl', x=100, y=100, w=200, h=25,
                  nearby_text='Name:', ref='edit-name'),
        RankInput(name='', ctype='EditControl', x=100, y=150, w=200, h=25,
                  nearby_text='Email:', ref='edit-email'),
    ]
    pick, ev = select_control(named, 'the field next to Email')
    assert pick is not None and pick.ref == 'edit-email', (pick, ev)
    assert ev.get('method') == 'ranked' and 'label_match' in ev, ev
    print('UNIT pure label_match path -> edit-email', ev, flush=True)


def test_unit_reading_order_not_tree_order():
    """Reversed input order must not change ordinal resolution."""
    from core.orchestrator.disambiguation import select_control
    rows = list(reversed(scratch_rows()))
    pick, ev = select_control(rows, 'first text field')
    assert pick is not None and pick.ref == 'edit1', (pick, ev)
    print('UNIT tree-order independence OK', flush=True)


def test_unit_router_patterns():
    from types import SimpleNamespace
    from core.orchestrator import IntelligenceRouter
    router = IntelligenceRouter()
    ts = SimpleNamespace(task_id='t', goal='')
    perc = SimpleNamespace(capture_method=99)
    cases = [
        ('Read the first text field.', 'first text field | EditControl'),
        ('Tell me what is in the second text field.',
         'second text field | EditControl'),
        ('What is in the second text field?',
         'second text field | EditControl'),
        ('Click the second button.', None),  # click path, not read
    ]
    for goal, want in cases:
        if want is None:
            continue
        r = router._try_local_reasoning(goal, perc, ts)
        assert r is not None, f'no route: {goal}'
        got = r.plan.steps[0].target_description
        assert got == want, f'{goal}: {got!r} != {want!r}'
    print('UNIT router read patterns OK', flush=True)


def test_unit_perf():
    import time as _t
    from core.orchestrator.disambiguation import select_control
    rows = scratch_rows()
    t0 = _t.perf_counter()
    for _ in range(200):
        select_control(rows, 'second text field')
    dt = (_t.perf_counter() - t0) / 200.0 * 1000.0
    print(f'UNIT ranker mean {dt:.2f}ms/lookup (no screenshot/OCR/model)',
          flush=True)
    assert dt < 50.0, f'ranker too slow: {dt:.1f}ms'


def user_idle_seconds():
    import ctypes

    class LII(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

    lii = LII()
    lii.cbSize = ctypes.sizeof(LII)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0


def live_e2e():
    """Full engine loop on the test-owned scratch window. Skips honestly
    when the user is active."""
    idle = user_idle_seconds()
    print(f'machine idle: {idle:.0f}s (gate: {IDLE_GATE_SECONDS:.0f}s)',
          flush=True)
    if idle < IDLE_GATE_SECONDS:
        print('LIVE E2E SKIPPED: user active; refusing to fight for '
              'foreground (would prove nothing)', flush=True)
        return 'SKIPPED'
    import subprocess
    import win32gui
    from core.tools import bring_to_foreground
    proc = subprocess.Popen(
        ['powershell', '-ExecutionPolicy', 'Bypass', '-File',
         r'C:\Users\duvvu\AppData\Local\Temp\opencode\scratch_winforms.ps1'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        hwnd = 0
        for _ in range(40):
            hwnd = win32gui.FindWindow(None, 'DUDE Grounding Scratch')
            if hwnd:
                break
            time.sleep(0.5)
        assert hwnd, 'scratch app did not appear'
        print('scratch hwnd:', hwnd, flush=True)
        assert bring_to_foreground(hwnd), 'could not focus scratch'
        time.sleep(1.0)
        from test_phase8_generic_app import build_stack
        stack = build_stack()
        try:
            from core.orchestrator import TaskType
            import asyncio
            for goal, marker, other in (
                    ('Read the first text field.', 'FIRST_MARKER',
                     'SECOND_MARKER'),
                    ('Read the second text field.', 'SECOND_MARKER',
                     'FIRST_MARKER')):
                st = asyncio.run(asyncio.wait_for(
                    stack['engine'].run(goal, TaskType.AUTOMATE),
                    timeout=180.0))
                assert st.is_complete(), \
                    f'{goal}: {st.failure_reason}'
                # Semantic proof: the READ PRODUCT must be this field's
                # value, not merely "a successful read".
                found = ''
                for step in (st.completed_steps or []):
                    v = getattr(step, 'verification', None)
                    actual = getattr(v, 'actual', None)
                    t = (getattr(actual, 'text_found', '') or '')
                    if t:
                        found += t + '\n'
                print(f'LIVE {goal} read={found.strip()[:60]!r}', flush=True)
                assert marker in found, \
                    f'WRONG FIELD: expected {marker}: {found[:120]!r}'
                assert other not in found, \
                    f'CONTAMINATION from other field: {found[:120]!r}'
            # Stale-safety: change value underneath, re-read fresh.
            with open(r'C:\Users\duvvu\AppData\Local\Temp\opencode'
                      r'\field1.value', 'w', encoding='utf-8') as f:
                f.write('FIRST_MARKER_V2')
            time.sleep(1.5)
            st = asyncio.run(asyncio.wait_for(
                stack['engine'].run('Read the first text field.',
                                    TaskType.AUTOMATE),
                timeout=180.0))
            assert st.is_complete(), f're-read: {st.failure_reason}'
            found2 = ''
            for step in (st.completed_steps or []):
                v = getattr(step, 'verification', None)
                actual = getattr(v, 'actual', None)
                found2 += (getattr(actual, 'text_found', '') or '') + '\n'
            assert 'FIRST_MARKER_V2' in found2, \
                f'stale read (old value kept): {found2[:120]!r}'
            print('LIVE stale-safety re-read got V2 value', flush=True)
        finally:
            try:
                import shutil
                shutil.rmtree(stack['tmp'], ignore_errors=True)
            except Exception:
                pass
        return 'FULL PASS'
    finally:
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, 0x0010, 0, 0)
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            os.remove(r'C:\Users\duvvu\AppData\Local\Temp\opencode'
                      r'\field1.value')
        except Exception:
            pass


def main():
    test_unit_first_second_fields()
    test_unit_buttons_generic()
    test_unit_label_association()
    test_unit_reading_order_not_tree_order()
    test_unit_router_patterns()
    test_unit_perf()
    verdict = live_e2e()
    print(f'PHASE 8 GROUNDING: units FULL PASS, live E2E {verdict}',
          flush=True)


if __name__ == '__main__':
    main()
