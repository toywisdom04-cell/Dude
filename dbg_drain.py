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
from core.orchestrator.pattern_store import get_pattern_store

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
time.sleep(12)
log(f'svc log_len={len(svc.observations)}')
recent = svc.observations.recent(n=20)
log(f'recent n={len(recent)}')
if recent:
    o = recent[0]
    log(f'obs0 app={o.active_app!r} title={o.window_title[:40]!r} '
        f'ctypes={o.control_types[:6]} change={o.change}')
    log('building detector')
    from core.orchestrator.observation_learner import PatternDetector
    ps = get_pattern_store(db_path=os.path.join(tmp, 'pats.db'))
    log('pattern store ok')
    det = PatternDetector(pattern_store=ps)
    log('detector ok; adding observation')
    det.add_observation(o)
    log(f'added; patterns={len(det._patterns)}')
    for k, p in det._patterns.items():
        log(f'  key={k[:80]!r} freq={p.frequency} actions={p.action_types}')
log('stopping service')
svc.stop()
log('DONE')