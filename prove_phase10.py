import os
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')


def log(msg):
    print(msg, flush=True)


def image_count():
    n = 0
    for r, d, f in os.walk('data'):
        for x in f:
            if x.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                n += 1
    return n


log('init')
from core.screentree import ScreenMap
from core.tools import init_screentree
from core.memory import Memory
from core.orchestrator import (
    PerceptionEngine, PerceptionService, PerceptualMemory,
    PerceptualMemoryPolicy,
)
from core.orchestrator.perception_service import FrameRing

smap = ScreenMap()
init_screentree(smap)
time.sleep(2)
log(f'smap={smap.available()}')


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
pm = PerceptualMemory(
    perception_service=None, memory=memory,
    policy=PerceptualMemoryPolicy())

# foreground baseline for TEST F
import win32gui
fg0 = win32gui.GetForegroundWindow()
log(f'fg_before={fg0} title={win32gui.GetWindowText(fg0)[:50]!r}')
imgs0 = image_count()

svc = PerceptionService(
    perception_engine=perception, capture_fn=_capture_fn, memory=memory,
    fast_hz=10.0, idle_hz=2.0, frame_ttl_seconds=60.0, max_frames=4)
svc.start()
pm.service = svc
log('service started; observing 14s')
time.sleep(14)
recent = svc.observations.recent(n=20)
log(f'svc obs_created={svc._counters["observations_created"]} '
    f'deduped={svc._counters["observations_deduped"]} log_len={len(svc.observations)} '
    f'recent={len(recent)}')
assert recent, 'no real observations produced'
obs = recent[-1]
log(f'OBS app={obs.active_app!r} title={obs.window_title[:45]!r} '
    f'change={obs.change} dialog={obs.dialog_kind} conf={obs.confidence:.2f}')

# TEST A: semantic memory creation from real observation
d = pm.record_observation(obs)
log(f'TEST-A layer={d.layer} reason={d.reason[:80]!r} persisted={d.persisted}')

# TEST B: duplicate compaction
ded0 = svc.observations.deduped
for _ in range(5):
    svc.observations.push(obs)
log(f'TEST-B log_dedup_delta={svc.observations.deduped - ded0} (5 identical pushes)')
p1 = pm.persist(obs, category='screen_state')
p2 = pm.persist(obs, category='screen_state')
log(f'TEST-B persist_again first={p1} second={p2} (second must be False)')

# TEST C: frame TTL/capacity (unit) + production policy
fr = FrameRing(max_frames=4, ttl_seconds=1.0)
for i in range(6):
    fr.push(b'frame-%d' % i, purpose='test')
log(f'TEST-C unit live_after_6push={fr.live_count()} (max 4)')
time.sleep(1.5)
log(f'TEST-C unit expired={fr.sweep()} live={fr.live_count()}')
log(f'TEST-C prod policy max={svc._frames._frames.maxlen} ttl={svc._frames._ttl}s')

# TEST D: cross-session persistence
key = f'10D-PROBE-{int(time.time())}'
memory.remember_fact(f'[percept probe] {key} app={obs.active_app}', category='screen_state')
mem2 = Memory()
got = mem2.recall_facts(key, limit=4)
log(f'TEST-D session2 retrieved={len(got)} match={bool(got and key in str(got[0]))}')

# TEST E: relevant retrieval
rel = pm.retrieve_relevant(obs.active_app or 'notepad', limit=5)
unrel = pm.retrieve_relevant('zzqq-no-such-thing-zzqq', limit=5)
log(f'TEST-E related_hits={len(rel)} unrelated_hits={len(unrel)} '
    f'sample={rel[0][:100] if rel else None!r}')

# TEST F: focus safety
fg1 = win32gui.GetForegroundWindow()
log(f'TEST-F fg_after={fg1} unchanged={fg0 == fg1}')

# TEST G: warehouse check
log(f'TEST-G frames={pm.frame_status()} images_before={imgs0} images_after={image_count()}')

# TEST H: provenance
facts = memory.facts_by_category('screen_state', limit=50)
tagged = [f for f in facts
          if isinstance(f, dict) and 'kind=observed' in f.get('fact', '')]
log(f'TEST-H screen_state_facts={len(facts)} with_observed_provenance={len(tagged)}')
if tagged:
    log(f'TEST-H sample={tagged[-1]["fact"][:160]!r}')
log('DONE')
svc.stop()