import os
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')


def log(msg):
    print(msg, flush=True)


log('imports')
from core.screentree import ScreenMap
from core.tools import init_screentree
from core.memory import Memory
from core.orchestrator import (
    PerceptionEngine, ProcedureStore, ProcedureLearner,
    IntelligenceRouter, get_observation_learner,
    PerceptionService,
)
from core.orchestrator.pattern_store import PatternStore, PersistedPattern
from core.orchestrator.parameter_extractor import get_parameter_extractor
from core.orchestrator.parameter_binder import ParameterBinder
from core.associations import get_associations

log('screentree')
smap = ScreenMap()
init_screentree(smap)
time.sleep(2)


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
    get_screentree=lambda: smap, get_ocr_fn=_ocr_fn, get_capture_fn=_capture_fn)
tmp = tempfile.mkdtemp()
proc_store = ProcedureStore(db_path=os.path.join(tmp, 'procedures.db'))
proc_learner = ProcedureLearner(procedure_store=proc_store, enable_learning=False)
router = IntelligenceRouter(procedure_store=proc_store, memory=memory)

log('start service')
svc = PerceptionService(
    perception_engine=perception, capture_fn=_capture_fn, memory=memory,
    fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
svc.start()
time.sleep(14)

obs = get_observation_learner(
    perception_service=svc, perception_engine=perception, memory=memory,
    procedure_store=proc_store, procedure_learner=proc_learner,
    intelligence_router=router, enable_learning=True)
r = obs.process_pending()
log(f'DRAIN delta={r["processed_delta"]} patterns={r["patterns_detected"]}')
pats = obs.get_patterns()
for p in pats:
    log(f'  PAT key={p["key"][:70]!r} freq={p["frequency"]} conf={p["confidence"]:.2f} '
        f'actions={p["actions"]}')

# ---- 9E-2: persist top REAL pattern, destroy, reload ----
db_path = os.path.join(tmp, 'obs_patterns.db')
if pats:
    top = max(pats, key=lambda p: (p['frequency'], p['confidence']))
    for p in obs._detector._patterns.values():
        if p.pattern_key == top['key']:
            obs._persist_pattern(p)
            # also persist explicitly to the session db file
            ps1 = PatternStore(db_path=db_path)
            ps1.save(PersistedPattern(
                pattern_key=p.pattern_key, app=p.app, window_title=p.window_title,
                control_sequence=p.control_sequence, action_types=p.action_types,
                frequency=p.frequency, first_seen=p.first_seen, last_seen=p.last_seen,
                contexts=p.contexts[-5:], confidence=p.confidence, promoted=p.promoted,
                app_sequence=p.app_sequence, workflow_id=p.workflow_id))
            rec1 = ps1.get(top['key'])
            log(f'9E2 session1 key={rec1.pattern_key[:60]!r} freq={rec1.frequency} '
                f'app={rec1.app!r} conf={rec1.confidence:.2f} ctx={len(rec1.contexts)}')
            break
    del obs
    del ps1
    ps2 = PatternStore(db_path=db_path)
    rec2 = ps2.get(top['key'])
    ok = (rec2 is not None and rec2.frequency == rec1.frequency
          and rec2.app == rec1.app and rec2.confidence == rec1.confidence
          and rec2.control_sequence == rec1.control_sequence)
    log(f'9E2 session2 freq={rec2.frequency} app={rec2.app!r} conf={rec2.confidence:.2f} '
        f'match={ok}')
    log(f'9E2 store_stats={ps2.get_stats()}')
else:
    log('9E2 SKIPPED: no patterns')

# ---- 9E-3: correlate (fresh learner on same live service) ----
obs2 = get_observation_learner(
    perception_service=svc, perception_engine=perception, memory=memory,
    procedure_store=proc_store, procedure_learner=proc_learner,
    intelligence_router=router, enable_learning=True)
obs2.process_pending()
corr = obs2.correlate_recent_patterns(window_seconds=600.0)
log(f'9E3 correlated={corr["correlated"]} apps={corr["apps"]} '
    f'nkeys={len(corr["keys"])} wf={str(corr["workflow_id"])[:8]}')

# ---- 9E-4: three real goal strings, one varying parameter family ----
ex = get_parameter_extractor()
goals = ['Open Calculator and calculate 25 times 4',
         'Open Calculator and calculate 37 times 6',
         'Open Calculator and calculate 8 + 8']
exts = [ex.extract(g, None).parameters for g in goals]
for g, e in zip(goals, exts):
    log(f'9E4 {g!r} -> op1={e.get("operand1")!r} op2={e.get("operand2")!r} '
        f'op={e.get("operator")!r}')
binder = ParameterBinder()
tmpl = 'calculate {operand1} times {operand2}'
b1 = binder._substitute(tmpl, {'operand1': exts[0]['operand1'], 'operand2': exts[0]['operand2']})
b2 = binder._substitute(tmpl, {'operand1': exts[1]['operand1'], 'operand2': exts[1]['operand2']})
b3 = binder._substitute('calculate {operand1} + {operand2}',
                        {'operand1': exts[2]['operand1'], 'operand2': exts[2]['operand2']})
log(f'9E4 bound={b1!r} | {b2!r} | {b3!r}')

# ---- 9E-5: associations write -> retrieve -> context ----
ab = get_associations()
ab._link('9E-TEST-notepad', '9E-TEST-type_text', rel='has_step', delta=0.9)
ab._link('9E-TEST-notepad', '9E-TEST-wf1', rel='has_workflow', delta=0.8)
ab.record_insight('9E-TEST workflow: 9E-TEST-notepad -> 9E-TEST-type_text',
                  kind='workflow_step', basis='9E-TEST-observation')
got = ab.associations_for('9E-TEST-notepad')
log(f'9E5 associations_for={got[:3]}')
ctx = obs2.get_context_for_app('9E-TEST-notepad')
log(f'9E5 context n={len(ctx)} sample={ctx[:2]}')

# ---- boundary: candidate in buffer, store empty ----
from core.orchestrator.procedure_learner import LearningCandidate
cand = LearningCandidate(
    goal='9E-TEST synthetic candidate (boundary check)', goal_type='AUTOMATE',
    context={'source': '9E-TEST-observation'}, steps=[
        {'description': 'x', 'action_type': 'click', 'target': 'y'}],
    parameters=[], verification_results=[], perception_requirements=[1],
    source_task_id='9E-TEST')
proc_learner._candidates['9E-TEST-boundary'] = cand
stats = proc_store.get_stats()
found = proc_store.find_by_goal('9E-TEST-boundary', min_confidence=0.0)
log(f'9E-BOUNDARY buffer={len(proc_learner._candidates)} store_total={stats["total_procedures"]} '
    f'retrieved={len(found)}')

# ---- screenshot lifecycle ----
log(f'9E-SHOT live={svc._frames.live_count()} seen={svc._counters["frames_seen"]} '
    f'expired={svc._frames.expired_deleted} max=4 ttl=60s')
try:
    obs2.stop()
except Exception as e:
    log(f'stop: {e}')
svc.stop()
log('DONE')