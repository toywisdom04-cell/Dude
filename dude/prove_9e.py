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
from core.tools import init_screentree
from core.memory import Memory
from core.orchestrator import (
    PerceptionEngine, ProcedureStore, ProcedureLearner,
    IntelligenceRouter, get_observation_learner,
    get_pattern_store, PerceptionService,
)
from core.orchestrator.parameter_extractor import get_parameter_extractor
from core.orchestrator.parameter_binder import ParameterBinder
from core.associations import get_associations


def build_stack(tmp):
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
    proc_store = ProcedureStore(db_path=os.path.join(tmp, 'procedures.db'))
    learner = ProcedureLearner(procedure_store=proc_store, enable_learning=False)
    router = IntelligenceRouter(procedure_store=proc_store, memory=memory)
    return smap, memory, perception, proc_store, learner, router, _capture_fn


async def main():
    tmp = tempfile.mkdtemp()
    smap, memory, perception, proc_store, proc_learner, router, _cap = build_stack(tmp)

    # ---- 9E-1: real service -> learner consumption ----
    svc = PerceptionService(
        perception_engine=perception, capture_fn=_cap, memory=memory,
        fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
    svc.start()
    await asyncio.sleep(25)
    obs = get_observation_learner(
        perception_service=svc, perception_engine=perception, memory=memory,
        procedure_store=proc_store, procedure_learner=proc_learner,
        intelligence_router=router, enable_learning=True)
    before = obs._processed_count
    r1 = obs.process_pending()
    await asyncio.sleep(6)
    r2 = obs.process_pending()
    print('9E1 processed_before=%d processed_after=%d delta=%d+%d '
          'patterns=%d ready=%d svc_obs_created=%d svc_deduped=%d svc_redundant=%d' % (
        before, r2['processed_after'], r1['processed_delta'], r2['processed_delta'],
        r2['patterns_detected'], r2['candidates_ready'],
        svc._counters['observations_created'], svc._counters['observations_deduped'],
        svc._counters['redundant_ticks_avoided']))

    # ---- 9E-2: persist real pattern, destroy, reload ----
    pats = obs.get_patterns()
    print('9E2 patterns_available=%d' % len(pats))
    reloaded_ok = False
    if pats:
        top = max(pats, key=lambda p: p['frequency'])
        print('9E2 top_pattern key=%r freq=%d conf=%.2f' % (
            top['key'], top['frequency'], top['confidence']))
        # persist via learner path (real detector object)
        for p in obs._detector._patterns.values():
            if p.pattern_key == top['key']:
                obs._persist_pattern(p)
                break
        db_path = os.path.join(tmp, 'obs_patterns.db')
        # NOTE: learner's store uses default data_dir path; also save explicitly here
        from core.orchestrator.pattern_store import PatternStore, PersistedPattern
        ps1 = PatternStore(db_path=db_path)
        for p in obs._detector._patterns.values():
            if p.pattern_key == top['key']:
                ps1.save(PersistedPattern(
                    pattern_key=p.pattern_key, app=p.app, window_title=p.window_title,
                    control_sequence=p.control_sequence, action_types=p.action_types,
                    frequency=p.frequency, first_seen=p.first_seen, last_seen=p.last_seen,
                    contexts=p.contexts[-5:], confidence=p.confidence, promoted=p.promoted,
                    app_sequence=p.app_sequence, workflow_id=p.workflow_id))
                break
        rec1 = ps1.get(top['key'])
        # destroy objects
        del obs
        del ps1
        # SESSION 2: fresh instances, same SQLite file
        ps2 = PatternStore(db_path=db_path)
        rec2 = ps2.get(top['key'])
        reloaded_ok = (rec2 is not None and rec2.frequency == rec1.frequency
                       and rec2.app == rec1.app and rec2.confidence == rec1.confidence)
        print('9E2 session1 freq=%d app=%r conf=%.2f | session2 freq=%d app=%r conf=%.2f | match=%s' % (
            rec1.frequency, rec1.app, rec1.confidence,
            rec2.frequency if rec2 else -1, rec2.app if rec2 else None,
            rec2.confidence if rec2 else -1.0, reloaded_ok))
    else:
        print('9E2 SKIPPED: no real patterns detected in window')

    # ---- 9E-3: multi-app correlation over real patterns ----
    obs2 = get_observation_learner(
        perception_service=svc, perception_engine=perception, memory=memory,
        procedure_store=proc_store, procedure_learner=proc_learner,
        intelligence_router=router, enable_learning=True)
    obs2.process_pending()
    corr = obs2.correlate_recent_patterns(window_seconds=600.0)
    print('9E3 correlated=%s apps=%s nkeys=%d workflow=%s' % (
        corr['correlated'], corr['apps'], len(corr['keys']),
        (corr['workflow_id'] or '')[:8]))

    # ---- 9E-4: varying values -> parameter + binder ----
    ex = get_parameter_extractor()
    goals = ['Open Calculator and calculate 25 times 4',
             'Open Calculator and calculate 37 times 6',
             'Open Calculator and calculate 8 + 8']
    exts = [ex.extract(g, None).parameters for g in goals]
    for g, e in zip(goals, exts):
        print('9E4 extract %r -> operand1=%r operand2=%r operator=%r' % (
            g, e.get('operand1'), e.get('operand2'), e.get('operator')))
    binder = ParameterBinder()
    tmpl = 'calculate {operand1} times {operand2}'
    bound = [binder._substitute(tmpl, {'operand1': e.get('operand1'),
                                       'operand2': e.get('operand2')}) for e in exts[:2]]
    bound.append(binder._substitute('calculate {operand1} + {operand2}',
                                    {'operand1': exts[2].get('operand1'),
                                     'operand2': exts[2].get('operand2')}))
    print('9E4 bound:', bound)
    print('9E4 literal_retained:', any(v in bound for v in ['25', '37']))

    # ---- 9E-5: associations write -> retrieve -> context ----
    ab = get_associations()
    ab._link('9E-TEST-notepad', '9E-TEST-type_text', rel='has_step', delta=0.9)
    ab.record_insight('9E-TEST workflow: 9E-TEST-notepad -> 9E-TEST-type_text',
                      kind='workflow_step', basis='9E-TEST-observation')
    got = ab.associations_for('9E-TEST-notepad')
    print('9E5 associations_for:', got[:3])
    ctx = obs2.get_context_for_app('9E-TEST-notepad')
    print('9E5 get_context_for_app n=%d sample=%r' % (len(ctx), ctx[:2]))

    # ---- boundary: candidate exists but store stays empty ----
    from core.orchestrator.procedure_learner import LearningCandidate
    from core.orchestrator.state import ProcedureParameter
    cand = LearningCandidate(
        goal='9E-TEST synthetic candidate (boundary check)',
        goal_type='AUTOMATE', context={'source': '9E-TEST-observation'},
        steps=[{'description': 'x', 'action_type': 'click', 'target': 'y'}],
        parameters=[], verification_results=[], perception_requirements=[1],
        source_task_id='9E-TEST')
    proc_learner._candidates['9E-TEST-boundary'] = cand
    stats = proc_store.get_stats()
    procs = proc_store.find_by_goal('9E-TEST-boundary', min_confidence=0.0)
    print('9E-BOUNDARY candidates=%d store_total=%d retrieved=%d' % (
        len(proc_learner._candidates), stats['total_procedures'], len(procs)))

    # ---- screenshot lifecycle ----
    print('9E-SHOT frames_live=%d seen=%d expired_deleted=%d' % (
        svc._frames.live_count(), svc._counters['frames_seen'],
        svc._frames.expired_deleted))
    try:
        obs2.stop()
    except Exception:
        pass
    svc.stop()
    print('DONE')


asyncio.run(main())