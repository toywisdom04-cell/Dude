import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')
os.environ['DUDE_ORCHESTRATOR_ENABLED'] = 'true'
os.environ['DUDE_USE_NEW_TASK_ENGINE'] = 'true'
os.environ['DUDE_USE_NEW_PERCEPTION'] = 'true'
os.environ['DUDE_USE_NEW_ACTION_EXECUTOR'] = 'true'
os.environ['DUDE_USE_REAL_EXECUTION'] = 'true'

from core.screentree import ScreenMap
from core.tools import init_screentree, execute_tool
from core.memory import Memory
from core.brain import Brain
from core.orchestrator import (
    TaskEngine, TaskType, PerceptionEngine,
    ActionExecutor, VerificationEngine, RecoveryEngine,
    IntelligenceRouter, ProcedureStore, ProcedureLearner,
)
import json


def build_engine():
    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(2)
    assert smap.available(), 'UIA unavailable'

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
    learner = ProcedureLearner(procedure_store=proc_store, enable_learning=False)
    router = IntelligenceRouter(procedure_store=proc_store, memory=memory)
    brain = Brain(Memory(), lambda *a: True)
    engine = TaskEngine(
        intelligence=brain, perception=perception,
        action_executor=ActionExecutor(perception=perception, memory=memory),
        verification=VerificationEngine(perception=perception),
        recovery=RecoveryEngine(), intelligence_router=router,
        memory=memory, procedure_learner=learner,
    )
    return engine, memory


def visible_windows():
    mem = Memory()
    out = execute_tool('list_running_apps', json.dumps({}), mem, lambda *a: True)
    return out


async def main():
    # Extractor fix check
    from core.orchestrator.parameter_extractor import get_parameter_extractor
    ex = get_parameter_extractor()
    r = ex.extract('Open Notepad and type hello world', None)
    print('EXTRACTOR app_name:', repr(r.parameters.get('app_name')),
          '| text:', repr(r.parameters.get('text')))

    engine, memory = build_engine()

    # Unseen app 1: Paint (single-verb goal, generic open_app skill)
    goal1 = 'Open Paint'
    print('TASK1:', goal1)
    st1 = await engine.run(goal1, TaskType.AUTOMATE)
    for i, sg in enumerate(st1.subgoals or []):
        print(f'  step{i}: {sg.description} [{sg.action_type}] '
              f'target={sg.target_description!r} completed={sg.completed}')
    vr1 = st1.verification_result if hasattr(st1, 'verification_result') else None
    print('  verification:', (vr1.success, vr1.evidence[:150]) if vr1 else None,
          '| failure:', st1.failure_reason)
    print('  windows now:', visible_windows()[:400])

    # Unseen app 2: File Explorer
    goal2 = 'Open File Explorer'
    print('TASK2:', goal2)
    st2 = await engine.run(goal2, TaskType.AUTOMATE)
    for i, sg in enumerate(st2.subgoals or []):
        print(f'  {i}: {sg.description} [{sg.action_type}] '
              f'target={sg.target_description!r} completed={sg.completed}')
    vr2 = st2.verification_result if hasattr(st2, 'verification_result') else None
    print('  verification:', (vr2.success, vr2.evidence[:150]) if vr2 else None,
          '| failure:', st2.failure_reason)
    print('  windows now:', visible_windows()[:400])


asyncio.run(main())