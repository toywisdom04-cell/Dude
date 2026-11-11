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
        get_observation_learner, PerceptionService,
    )
    from core.orchestrator.parameter_binder import ParameterBinder

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
        get_screentree=lambda: smap, get_ocr_fn=_ocr_fn, get_capture_fn=_capture_fn)
    tmp = tempfile.mkdtemp()
    proc_store = ProcedureStore(db_path=os.path.join(tmp, 'procedures.db'))
    proc_learner = ProcedureLearner(procedure_store=proc_store, enable_learning=False)
    router = IntelligenceRouter(procedure_store=proc_store, memory=memory)
    brain = Brain(Memory(), lambda *a: True)
    engine = TaskEngine(
        intelligence=brain, perception=perception,
        action_executor=ActionExecutor(perception=perception, memory=memory),
        verification=VerificationEngine(perception=perception),
        recovery=RecoveryEngine(), intelligence_router=router,
        memory=memory, procedure_learner=proc_learner)

    svc = PerceptionService(
        perception_engine=perception, capture_fn=_capture_fn, memory=memory,
        fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
    svc.start()
    obs = get_observation_learner(
        perception_service=svc, perception_engine=perception, memory=memory,
        procedure_store=proc_store, procedure_learner=proc_learner,
        intelligence_router=router, enable_learning=True)

    goals = ['Open Calculator and calculate 25 times 4',
             'Open Calculator and calculate 37 times 6',
             'Open Calculator and calculate 8 + 8']
    for g in goals:
        log(f'EXEC: {g}')
        st = await engine.run(g, TaskType.AUTOMATE)
        log(f'  done steps={st.current_step}/{len(st.subgoals or [])} '
            f'state={engine.state}')
        r = obs.process_pending(goal=g)
        log(f'  drained delta={r["processed_delta"]} patterns={r["patterns_detected"]}')

    # Find the calculator pattern that accumulated goal-tagged contexts
    pats = [p for p in obs._detector._patterns.values()
            if any(isinstance(c.get("goal"), str) for c in p.contexts)]
    log(f'goal_tagged_patterns={len(pats)}')
    for p in pats:
        gs = sorted({c.get("goal") for c in p.contexts if c.get("goal")})
        log(f'  key={p.pattern_key[:60]!r} freq={p.frequency} goals={len(gs)}')

    # Merge goal-tagged contexts across same-app patterns for generalization
    from core.orchestrator.observation_learner import ObservedPattern
    merged_contexts = []
    for p in pats:
        merged_contexts.extend(p.contexts)
    log(f'merged_contexts={len(merged_contexts)}')
    synth = ObservedPattern(
        pattern_key='calc|multi|generalization-probe',
        app='calculator', window_title='Calculator',
        control_sequence=pats[0].control_sequence if pats else ['ButtonControl'],
        action_types=pats[0].action_types if pats else ['click'],
        frequency=len(merged_contexts), contexts=merged_contexts,
        confidence=0.8, app_sequence=['calculator'])
    params = obs._builder._extract_parameters(synth)
    for pr in params:
        log(f'  PARAM {pr.name}: type={pr.type} required={pr.required} '
            f'default={pr.default!r} desc={pr.description[:70]!r}')

    # Bind a NEW runtime value D through the existing ParameterBinder
    binder = ParameterBinder()
    tmpl = 'calculate {operand1} times {operand2}'
    out = binder._substitute(tmpl, {'operand1': '9', 'operand2': '9'})
    log(f'BOUND D: {out!r}')
    procs = proc_store.find_by_goal('calculate', min_confidence=0.0)
    log(f'store_total={proc_store.get_stats()["total_procedures"]} '
        f'retrieved_for_calc={len(procs)}')

    try:
        obs.stop()
    except Exception as e:
        log(f'stop: {e}')
    svc.stop()
    log('DONE')


asyncio.run(main())