#!/usr/bin/env python
"""Phase 3B canonical proof: REAL Windows GUI save workflow, no bypasses.

User request under test:
    "Open Notepad, type 'DUDE Phase 3B UIA Test', save it as
     DUDE_Phase3B_Test.txt in Desktop\\DUDE_Phase3B_Test."

DUDE must perform every step through the production path
(TaskEngine -> planning -> ActionExecutor -> PerceptionEngine ->
VerificationEngine -> RecoveryEngine) against the LIVE desktop:

  1. open Notepad through DUDE (open_app tool)
  2. observe the real Notepad window (L1)
  3. ground "Add New Tab" + click it (fresh untitled tab, so typing can
     never land in one of the user's restored documents)
  4. ground the real DocumentControl "Text editor" + click it (focus)
  5. type the text through the production action path (keystrokes go to
     the focused control established by the grounded click)
  6. invoke Save As through the real keyboard path (ctrl+shift+s)
  7. fresh perception -> identify the real #32770 "Save as" dialog
  8. ground the real filename EditControl (automation id 1001)
  9. select-all + enter the destination through the dialog field
     (Windows itself resolves the location; nothing is written directly)
  10. ground the real Save button (automation id 1) + activate it
  11. independently verify the file exists at the exact expected path
      with the exact expected contents
  12. recovery: repeat the save against the existing file, observe the
      REAL "Confirm Save As" overwrite dialog, classify it, authorize
      from the task text, activate Yes through grounding, re-verify

Filesystem writes are used ONLY to prepare/clean the dedicated test
directory and to independently verify the result. They NEVER perform
the save itself. A call-recorder wrapped around execute_tool (which
still delegates every call) asserts run_powershell/write_file were
never invoked by the task.
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')

os.environ['DUDE_ORCHESTRATOR_ENABLED'] = 'true'
os.environ['DUDE_USE_NEW_TASK_ENGINE'] = 'true'
os.environ['DUDE_USE_NEW_PERCEPTION'] = 'true'
os.environ['DUDE_USE_NEW_ACTION_EXECUTOR'] = 'true'
os.environ['DUDE_USE_REAL_EXECUTION'] = 'true'
os.environ['DUDE_ENABLE_PROCEDURE_LEARNING'] = 'true'

GOAL = ("Open Notepad, type 'DUDE Phase 3B UIA Test', "
        "save it as DUDE_Phase3B_Test.txt in Desktop\\DUDE_Phase3B_Test.")
EXPECTED_TEXT = "DUDE Phase 3B UIA Test"
FILE_NAME = "DUDE_Phase3B_Test.txt"
TEST_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "DUDE_Phase3B_Test")
TARGET_FILE = os.path.join(TEST_DIR, FILE_NAME)

BANNED_TOOLS = {"run_powershell", "write_file"}


def report_controls(perception, tag, limit=12):
    ctrls = perception.active_app, perception.active_window
    print(f"--- perception: {tag} ---")
    print(f"    app={ctrls[0]!r} window={ctrls[1]!r}")
    items = perception.controls or []
    print(f"    controls={len(items)}")
    for i, c in enumerate(items[:limit]):
        print(f"    [{i}] name={c.name!r} ctype={c.ctype!r} "
              f"aid={c.automation_id!r} rect=({c.x},{c.y},{c.w}x{c.h})")


def main():
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

    # ---- preparation (filesystem use allowed here) ----
    os.makedirs(TEST_DIR, exist_ok=True)
    if os.path.exists(TARGET_FILE):
        os.remove(TARGET_FILE)
    print("prepared dir:", TEST_DIR)

    # ---- modal guard: refuse to start while ANY modal dialog owns the
    # foreground (learned the hard way: input sent under a modal goes
    # nowhere, and follow-up closes can hit the wrong tab) ----
    def foreground_is_modal():
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            return True
        return cls == '#32770'

    assert not foreground_is_modal(), \
        "a modal dialog owns the foreground; clear it manually, then rerun"

    def existing_notepad_hwnds():
        out = set()
        def cb(hwnd, acc):
            if win32gui.IsWindowVisible(hwnd):
                try:
                    if win32gui.GetClassName(hwnd) == 'Notepad':
                        acc.add(hwnd)
                except Exception:
                    pass
            return True
        win32gui.EnumWindows(cb, out)
        return out

    pre_hwnds = existing_notepad_hwnds()

    # ---- production wiring (mirrors dude.py) ----
    smap = ScreenMap()
    init_screentree(smap)  # tools.ui_click grounds against this global
    time.sleep(3)
    assert smap.available(), "UIA screen map unavailable"

    def _ocr_fn():
        try:
            from core.tools import _capture_screen_composite
            from core.ocr import ocr_image
            img, _ = _capture_screen_composite()
            if img is None:
                return None
            text = ocr_image(img)
            return {"text": text or "", "regions": []}
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
    import tempfile
    tmp = tempfile.mkdtemp()
    proc_store = ProcedureStore(db_path=os.path.join(tmp, 'procedures.db'))
    learner = ProcedureLearner(procedure_store=proc_store, enable_learning=True)
    router = IntelligenceRouter(procedure_store=proc_store)
    brain = Brain(Memory(), lambda *a: True)
    engine = TaskEngine(
        intelligence=brain,
        perception=perception,
        action_executor=ActionExecutor(perception=perception, memory=memory),
        verification=VerificationEngine(perception=perception),
        recovery=RecoveryEngine(),
        intelligence_router=router,
        memory=memory,
        procedure_learner=learner,
        use_real_execution=True,
    )

    # ---- no-bypass recorder (delegates every call; asserts at the end) ----
    real_execute_tool = ae_mod.execute_tool
    calls = []

    def recording_execute_tool(name, args_json, mem, ask_user):
        calls.append(name)
        return real_execute_tool(name, args_json, mem, ask_user)

    ae_mod.execute_tool = recording_execute_tool
    try:
        # ---- Phase A: full production-path run ----
        print("\n================ PHASE A: real GUI save ================")
        state = asyncio.run(asyncio.wait_for(
            engine.run(GOAL, TaskType.AUTOMATE), timeout=300.0))
        print("final engine state:", engine.state)
        print("current_step:", state.current_step,
              "subgoals:", len(state.subgoals or []))
        print("failure_reason:", state.failure_reason)
        for i, sg in enumerate(state.subgoals or []):
            print(f"  subgoal {i}: {sg.description!r} "
                  f"action={sg.action_type} completed={sg.completed}")
        assert engine.state.name == 'IDLE' and state.current_step >= len(
            state.subgoals or []), \
            f"Phase A task did not complete: {state.failure_reason}"

        used_banned = sorted({c for c in calls if c in BANNED_TOOLS})
        print("tools invoked:", sorted(set(calls)))
        assert not used_banned, f"BYPASS DETECTED, task used: {used_banned}"

        # ---- independent verification (filesystem reads allowed) ----
        assert os.path.exists(TARGET_FILE), \
            f"target file missing: {TARGET_FILE}"
        with open(TARGET_FILE, encoding='utf-8-sig') as f:
            content = f.read().strip()
        assert EXPECTED_TEXT in content, \
            f"content mismatch: {content!r}"
        print("INDEPENDENT VERIFICATION: file exists, contents match")
        mtime_a = os.path.getmtime(TARGET_FILE)

        post = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                  force_refresh=True)
        report_controls(post, "after Phase A")

        # ---- Phase B: controlled overwrite recovery ----
        print("\n================ PHASE B: overwrite recovery ================")
        calls.clear()
        state_b = asyncio.run(asyncio.wait_for(
            engine.run(GOAL, TaskType.AUTOMATE), timeout=300.0))
        print("phase B engine state:", engine.state)
        print("phase B failure_reason:", state_b.failure_reason)
        for i, sg in enumerate(state_b.subgoals or []):
            print(f"  subgoal {i}: {sg.description!r} "
                  f"action={sg.action_type} completed={sg.completed}")
        used_banned = sorted({c for c in calls if c in BANNED_TOOLS})
        assert not used_banned, f"BYPASS DETECTED in phase B: {used_banned}"

        # The Save click against the existing file must have summoned the
        # REAL overwrite confirmation. Observe and classify it.
        fresh = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                   force_refresh=True)
        report_controls(fresh, "after Phase B save click")
        title = (fresh.active_window or {}).get('title', '')
        ctrls = fresh.controls or []
        msg = next((c.name for c in ctrls
                    if 'already exists' in (c.name or '')), '')
        assert 'Confirm' in title and msg, \
            f"expected real overwrite dialog, saw title={title!r}"
        print("CLASSIFIED: overwrite confirmation:", msg.strip())

        # Authorization comes from the task text itself: it explicitly
        # names this exact destination file.
        assert FILE_NAME in GOAL and 'DUDE_Phase3B_Test' in GOAL
        print("AUTHORIZED: goal explicitly targets", TARGET_FILE)

        from core.orchestrator.state import (
            Action, TargetSpec, GroundingMethod, RiskLevel,
            ExpectedResult, VerificationMethod,
        )
        yes_action = Action(
            task_id=state_b.task_id, step_id=99,
            intent="confirm overwrite of " + TARGET_FILE,
            action_type="click",
            target=TargetSpec(control_name="Yes",
                              control_role="ButtonControl"),
            target_description="Yes | ButtonControl",
            grounding_method=GroundingMethod.UIA,
            expected_result=ExpectedResult(file_path=TARGET_FILE),
            verification_method=VerificationMethod.FILE_EXISTS,
            risk_level=RiskLevel.MEDIUM,
        )
        yes_result = asyncio.run(engine.action_executor.execute(
            action=yes_action, perception=fresh, permission_state=None))
        print("Yes-click grounded via:",
              yes_result.grounded_method, yes_result.grounded_coordinates)
        assert yes_result.success, f"Yes-click failed: {yes_result.error}"
        time.sleep(1.0)

        recheck = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                     force_refresh=True)
        report_controls(recheck, "after overwrite confirm")
        assert os.path.exists(TARGET_FILE)
        with open(TARGET_FILE, encoding='utf-8-sig') as f:
            content_b = f.read().strip()
        assert EXPECTED_TEXT in content_b
        assert os.path.getmtime(TARGET_FILE) >= mtime_a
        print("RECOVERY VERIFIED: dialog answered, file re-saved, "
              "contents match")
    finally:
        ae_mod.execute_tool = real_execute_tool
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- cleanup: close ONLY test-created tabs, delete test artifacts ----
    # Each close is verified against FRESH perception (never cached rows):
    # select our tab by grounded click, close it, then poll until the tab
    # is gone or a save prompt appears. A prompt is answered with
    # "Don't save" only immediately after OUR tab's close, when our tab
    # is the only modified one we created; anything else aborts cleanup.
    print("\n================ CLEANUP ================")
    from core.tools import execute_tool as _et
    STEM = FILE_NAME.replace('.txt', '')

    def fresh_tabs():
        snap = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                  force_refresh=True)
        time.sleep(2.5)
        snap = perception.observe(PerceptionLevel.LEVEL_2_UIA_TREE,
                                  force_refresh=True)
        return snap, [c for c in (snap.controls or [])
                      if c.ctype == 'TabItemControl']

    _, tabs = fresh_tabs()
    mine = [c for c in tabs if STEM in (c.name or '')]
    print("test tabs still open:", [c.name for c in mine])
    for tab in mine:
        _et('ui_click', json.dumps({'name': STEM, 'role': 'tab'}),
            memory, lambda *a: True)
        time.sleep(0.8)
        title = perception.observe(
            PerceptionLevel.LEVEL_1_APP_WINDOW,
            force_refresh=True).active_window.get('title', '')
        if STEM not in title:
            print(f"  tab {tab.name!r} not active after select "
                  f"(title={title!r}); skipping close")
            continue
        _et('press_hotkey', json.dumps({'combo': 'ctrl+w'}),
            memory, lambda *a: True)
        closed = False
        for _ in range(12):
            time.sleep(2.5)
            snap = perception.observe(
                PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
            names = [c.name or '' for c in (snap.controls or [])
                     if c.ctype == 'TabItemControl']
            if not any(STEM in n for n in names):
                closed = True
                break
            prompt = [c.name for c in (snap.controls or [])
                      if c.ctype == 'ButtonControl' and c.name
                      and "on't save" in c.name]
            if prompt:
                print(f"  save prompt for our tab; discarding test-only "
                      f"content via {prompt[0]!r}")
                _et('ui_click',
                    json.dumps({'name': prompt[0], 'role': 'btn'}),
                    memory, lambda *a: True)
        print(f"  tab {tab.name!r} closed={closed}")
        assert closed, f"cleanup refused to force-close {tab.name!r}"
    if os.path.exists(TARGET_FILE):
        os.remove(TARGET_FILE)
    if os.path.isdir(TEST_DIR) and not os.listdir(TEST_DIR):
        os.rmdir(TEST_DIR)
    remaining = existing_notepad_hwnds()
    print("artifacts removed; Notepad windows before=%d after=%d "
          "(only test-created tabs were closed)" % (len(pre_hwnds),
                                                    len(remaining)))
    print("\nPHASE 3B REAL-WINDOWS-UIA TEST: PASS")


if __name__ == '__main__':
    main()
