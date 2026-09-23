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
    from core.orchestrator import (
        PerceptionEngine, ProcedureStore, ProcedureLearner,
        IntelligenceRouter, get_observation_learner,
        PerceptionService,
    )
    log('init screentree')
    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(2)
    log(f'smap available={smap.available()}')

    def _ocr_fn():
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
    proc_learner = ProcedureLearner(procedure_store=proc_store, enable_learning=False)
    router = IntelligenceRouter(procedure_store=proc_store, memory=memory)

    log('start service')
    svc = PerceptionService(
        perception_engine=perception, capture_fn=_capture_fn, memory=memory,
        fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
    svc.start()
    log('service started; sleeping 12s')
    await asyncio.sleep(12)
    log('woke; building learner')
    obs = get_observation_learner(
        perception_service=svc, perception_engine=perception, memory=memory,
        procedure_store=proc_store, procedure_learner=proc_learner,
        intelligence_router=router, enable_learning=True)
    log(f'learner built; svc obs_created={svc._counters["observations_created"]} '
        f'deduped={svc._counters["observations_deduped"]} '
        f'redundant={svc._counters["redundant_ticks_avoided"]} '
        f'log_len={len(svc.observations)}')
    r = obs.process_pending()
    log(f'drain1: {r}')
    await asyncio.sleep(6)
    r2 = obs.process_pending()
    log(f'drain2: {r2}')
    pats = obs.get_patterns()
    log(f'patterns={len(pats)}')
    for p in pats[:5]:
        log(f'  key={p["key"][:90]!r} freq={p["frequency"]} conf={p["confidence"]:.2f} '
            f'actions={p["actions"]}')
    try:
        obs.stop()
    except Exception as e:
        log(f'learner stop: {e}')
    svc.stop()
    log('stopped; DONE')


asyncio.run(main())