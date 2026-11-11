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


def log(msg):
    print(msg, flush=True)


async def main():
    from core.screentree import ScreenMap
    from core.tools import init_screentree
    from core.memory import Memory
    from core.brain import Brain
    from core.orchestrator import (
        TaskEngine, TaskType, PerceptionEngine,
        ActionExecutor, VerificationEngine, RecoveryEngine,
        IntelligenceRouter, ProcedureStore, ProcedureLearner,
        PerceptionService, PerceptualMemory,
    )
    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(2)
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
        get_screentree=lambda: smap, get_ocr_fn=_ocr_fn, get_capture_fn=_capture_fn)
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
        memory=memory, procedure_learner=learner)
    svc = PerceptionService(
        perception_engine=perception, capture_fn=_capture_fn, memory=memory,
        fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
    svc.start()
    pm = PerceptualMemory(perception_service=svc, memory=memory)

    # SESSION 1: real GUI task while observing
    goal = 'Open Calculator and calculate 8 + 8'
    log(f'GUI TASK: {goal}')
    st = await engine.run(goal, TaskType.AUTOMATE)
    log(f'  task steps={st.current_step}/{len(st.subgoals or [])} state={engine.state}')
    time.sleep(4)
    recent = svc.observations.recent(n=20)
    meaningful = [o for o in recent
                  if o.change in ('action_relevant', 'task_relevant', 'recovery_relevant')
                  or o.dialog_kind not in ('none', '')]
    log(f'  observations={len(recent)} meaningful={len(meaningful)}')
    persisted = []
    for o in meaningful[:3]:
        d = pm.record_observation(o)
        log(f'  route layer={d.layer} persisted={d.persisted} '
            f'app={o.active_app!r} change={o.change} conf={o.confidence:.2f}')
        if d.persisted:
            persisted.append(o.to_fact()[:60])
    log(f'PERSISTED_N={len(persisted)}')

    # SESSION 2: fresh components, retrieve
    mem2 = Memory()
    hits = []
    for probe in ['Calculator', 'calculator']:
        hits = mem2.recall_facts(probe, limit=8)
        if hits:
            break
    log(f'RETRIEVED_N={len(hits)} sample={str(hits[0])[:140] if hits else None!r}')
    svc.stop()
    log('DONE')


asyncio.run(main())