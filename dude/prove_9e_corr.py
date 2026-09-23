import os
import sys
import tempfile
import time

sys.path.insert(0, r'E:\Dude\dude')


def log(msg):
    print(msg, flush=True)


from core.screentree import ScreenMap
from core.tools import init_screentree
from core.memory import Memory
from core.orchestrator import (
    PerceptionEngine, ProcedureStore, ProcedureLearner,
    IntelligenceRouter, get_observation_learner,
    PerceptionService,
)

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

svc = PerceptionService(
    perception_engine=perception, capture_fn=_capture_fn, memory=memory,
    fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
svc.start()
log('service started; observing 45s for natural app transitions')
time.sleep(45)
obs = get_observation_learner(
    perception_service=svc, perception_engine=perception, memory=memory,
    procedure_store=proc_store, procedure_learner=proc_learner,
    intelligence_router=router, enable_learning=True)
r = obs.process_pending()
log(f'drain delta={r["processed_delta"]} patterns={r["patterns_detected"]}')
for p in obs.get_patterns():
    log(f'  key={p["key"][:70]!r} freq={p["frequency"]} actions={p["actions"]}')
corr = obs2.correlate_recent_patterns(window_seconds=600.0) if False else obs.correlate_recent_patterns(window_seconds=600.0)
log(f'correlated={corr["correlated"]} apps={corr["apps"]} nkeys={len(corr["keys"])} '
    f'wf={str(corr["workflow_id"])[:8]} obs_total={corr.get("observations", 0)}')
try:
    obs.stop()
except Exception as e:
    log(f'stop: {e}')
svc.stop()
log('DONE')