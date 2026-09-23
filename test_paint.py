import sys
sys.path.insert(0, r'E:\Dude\dude')

import json
import asyncio
import os
import tempfile
import sys

os.environ['DUDE_ORCHESTRATOR_ENABLED'] = 'true'
os.environ['DUDE_USE_NEW_TASK_ENGINE'] = 'true'
os.environ['DUDE_USE_NEW_PERCEPTION'] = 'true'
os.environ['DUDE_USE_NEW_ACTION_EXECUTOR'] = 'true'
os.environ['DUDE_USE_REAL_EXECUTION'] = 'true'

from core.screentree import ScreenMap
from core.tools import init_screentree
from core.memory import Memory
from core.brain import Brain
from core.orchestrator import (
    TaskEngine, TaskType, PerceptionEngine, PerceptionLevel,
    ActionExecutor, VerificationEngine, RecoveryEngine,
    IntelligenceRouter, ProcedureStore, ProcedureLearner,
    get_observation_learner,
    PerceptionService,
)

async def test_paint():
    smap = ScreenMap()
    init_screentree(smap)
    import time
    time.sleep(2)
    assert smap.available(), 'UIA screen map unavailable'
    
    def _ocr_fn():
        try:
            from core.tools import _capture_screen_composite
            from core.ocr import ocr_image
            img, _ = _capture_screen_composite()
            if img is None:
                return None
            text = ocr_image(img)
            return {'text': text or '', 'regions': []}
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
    learner = ProcedureLearner(procedure_store=proc_store, enable_learning=False)
    router = IntelligenceRouter(procedure_store=proc_store, memory=memory)
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
    )
    
    # Test with Paint (unseen app)
    goal = 'Open Paint and draw a line'
    print('TEST: Paint (unseen app)')
    print('TASK:', goal)
    state = await engine.run(goal, TaskType.AUTOMATE)
    print('RESULT: state=' + str(engine.state) + ', steps=' + str(state.current_step) + '/' + str(len(state.subgoals or [])))
    for i, sg in enumerate(state.subgoals or []):
        print(f'  {i}: {sg.description} [{sg.action_type}] completed={sg.completed}')

import tempfile
import os
import asyncio
asyncio.run(test_paint())