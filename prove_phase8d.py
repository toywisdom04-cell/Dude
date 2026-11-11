import asyncio
import json
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


async def main():
    engine, memory = build_engine()
    # Reclaim foreground from the stuck Task Manager probe window
    print('reclaim:', execute_tool(
        'open_app', json.dumps({'name': 'notepad'}),
        memory, lambda *a: True)[:80])
    time.sleep(2)
    goal = r'Open File Explorer at C:\Windows'
    print('TASK:', goal)
    st = await engine.run(goal, TaskType.AUTOMATE)
    for i, sg in enumerate(st.subgoals or []):
        print(f'  step{i}: {sg.description} [{sg.action_type}] '
              f'target={sg.target_description!r} completed={sg.completed}')
    vr = getattr(st, 'verification_result', None)
    print('  verification:', (vr.success, vr.evidence[:160]) if vr else None,
          '| failure:', (st.failure_reason or '')[:160])


asyncio.run(main())