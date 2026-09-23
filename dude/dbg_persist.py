import os
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')


def log(msg):
    print(msg, flush=True)


from core.screentree import ScreenMap
from core.tools import init_screentree
from core.memory import Memory
from core.orchestrator import (
    PerceptionEngine, PerceptionService, PerceptualMemory,
)

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
svc = PerceptionService(
    perception_engine=perception, capture_fn=_capture_fn, memory=memory,
    fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
svc.start()
time.sleep(12)
pm = PerceptualMemory(perception_service=svc, memory=memory)
recent = svc.observations.recent(n=20)
log(f'recent={len(recent)}')
for o in recent:
    if o.change in ('action_relevant', 'task_relevant', 'recovery_relevant') \
            or o.dialog_kind not in ('none', ''):
        d = pm.record_observation(o)
        log(f'layer={d.layer} persisted={d.persisted} reason={d.reason[:100]!r}')
        # manual duplicate diagnosis
        fact = o.to_fact()
        probe = fact[:60]
        cands = memory.recall_facts(probe, limit=4)
        log(f'  probe={probe!r} recall_hits={len(cands)}')
        for c in cands:
            s = str(c)
            log(f'    hit_match={fact[:70].lower() in s.lower()} hit={s[:100]!r}')
        break
svc.stop()
log('DONE')