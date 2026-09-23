#!/usr/bin/env python
"""Phase 12 slice tests: continuous perception -> live scene -> voice.

First vertical slice only: adaptive tiers, scene deltas, TTL/disposal,
classification + safe promotion, live-scene voice answers, compaction
bounds, staleness. Deterministic unit-level tests with a fake engine
where the loop would be slow; no BANNED tools, no cloud.
"""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, r'E:\Dude\dude')

BANNED_TOOLS = {"run_powershell", "write_file"}


class FakeMemory:
    def __init__(self):
        self.messages = []
        self.facts = []

    def add_message(self, role, content):
        self.messages.append((role, content))

    def remember_fact(self, fact, category='general'):
        self.facts.append((category, fact))
        return True


class FakeEngine:
    def __init__(self, app='fake.exe', title='fake window'):
        self.app = app
        self.title = title
        self.calls = 0

    def observe(self, level=None, force_refresh=False):
        self.calls += 1
        return SimpleNamespace(
            controls=[], active_app=self.app,
            active_window={'title': self.title, 'hwnd': 111,
                           'wclass': 'FakeClass'},
            focused_control=None)


def make_service(**kw):
    from core.orchestrator.perception_service import PerceptionService
    kw.setdefault('perception_engine', FakeEngine())
    kw.setdefault('memory', FakeMemory())
    return PerceptionService(**kw)


def make_controller(**kw):
    from core.orchestrator.realtime_voice import (
        RealtimeInteractionController, FakeTTSBackend, FakeSTTBackend)
    kw.setdefault('tts', FakeTTSBackend(word_ms=10.0))
    kw.setdefault('stt', FakeSTTBackend(transcript='hello dude'))
    kw.setdefault('memory', FakeMemory())
    c = RealtimeInteractionController(**kw)
    c.start_listening()
    return c


def obs(**kw):
    from core.orchestrator.perception_service import SemanticObservation
    base = dict(captured_at=time.time(), active_app='a.exe',
                window_title='w', hwnd=1, focused_name='f',
                dialog_kind='none', change='low', confidence=0.5)
    base.update(kw)
    return SemanticObservation(**base)


def test1_stable_backoff():
    svc = make_service()
    assert svc.set_activity('STABLE') == 'STABLE'
    assert svc._fast_interval == 1.0 and svc._idle_interval == 2.0, \
        (svc._fast_interval, svc._idle_interval)
    assert svc.set_activity('bogus') == 'NORMAL'
    print('TEST1 FULL PASS stable tier sleeps', flush=True)


def test2_change_acceleration():
    from core.orchestrator.perception_service import ChangeClass
    svc = make_service()
    svc.set_activity('NORMAL')
    assert svc._burst_until <= time.time(), 'burst pre-armed?'
    svc._on_change({}, ChangeClass.ACTION_RELEVANT, ['window_appeared:x'])
    assert svc._burst_until > time.time(), 'no burst after action change'
    print('TEST2 FULL PASS action change triggers burst', flush=True)


def test3_uia_change_detection():
    from core.orchestrator.perception_service import compute_scene_delta
    a = obs(focused_name='addr', dialog_kind='none')
    b = obs(focused_name='profile', dialog_kind='menu')
    d = compute_scene_delta(a, b)
    assert d.get('dialog_added') == 'menu', d
    assert d.get('focus_changed'), d
    c = obs(focused_name='profile', dialog_kind='none')
    d2 = compute_scene_delta(b, c)
    assert d2.get('dialog_removed') == 'menu', d2
    print('TEST3 FULL PASS focus + dialog deltas', flush=True)


def test4_screenshot_ttl():
    from core.orchestrator.perception_service import FrameRing
    ring = FrameRing(max_frames=2, ttl_seconds=0.5)
    ring.push(b'frame-bytes', 'test')
    assert ring.live_count() == 1
    time.sleep(0.7)
    assert ring.sweep() == 1, 'TTL expiry failed'
    assert ring.live_count() == 0
    print('TEST4 FULL PASS TTL expiry', flush=True)


def test5_screenshot_disposal():
    from PIL import Image
    svc = make_service(frame_ttl_seconds=0.5,
                       capture_fn=lambda: Image.new('RGB', (8, 8)))
    data = svc.capture_frame('p12test')
    assert data, 'capture failed'
    assert 'capture_ms' in svc.metrics.summary(), 'no capture metric'
    time.sleep(0.7)
    svc._frames.sweep()
    assert svc._frames.live_count() == 0, 'frame retained past TTL'
    assert len(svc._frames._frames) == 0, 'bytes still referenced'
    print('TEST5 FULL PASS disposal, no retained bytes', flush=True)


def test6_semantic_scene_update():
    svc = make_service()
    o1 = obs(active_app='a.exe', window_title='one')
    o2 = obs(active_app='b.exe', window_title='two')
    with svc._lock:
        svc._last_observation = o1
        svc._scene_version = 1
    s1 = svc.get_scene()
    assert (s1.active_app, s1.window_title, s1.scene_version) == \
        ('a.exe', 'one', 1), vars(s1)
    with svc._lock:
        svc._last_observation = o2
        svc._scene_version = 2
    s2 = svc.get_scene()
    assert s2.active_app == 'b.exe' and s2.scene_version == 2
    assert 'scene_update_ms' in svc.metrics.summary()
    print('TEST6 FULL PASS scene versions 1->2', flush=True)


def test7_secret_rejection():
    from core.orchestrator.perception_service import ObservationClass
    svc = make_service()
    evil = obs(summary='user typed password=hunter2 here',
               change='task_relevant')
    cls, reason = svc.classify_observation(evil)
    assert cls is ObservationClass.SECRET_SENSITIVE, (cls, reason)
    assert svc.promote(evil) is False, 'secret promoted!'
    assert svc._memory.facts == [] and svc._memory.messages == [], \
        'secret touched memory'
    print('TEST7 FULL PASS secret never persists', flush=True)


def test8_transient_rejection():
    from core.orchestrator.perception_service import ObservationClass
    svc = make_service()
    dull = obs(change='low')
    cls, _ = svc.classify_observation(dull)
    assert cls in (ObservationClass.TRANSIENT, ObservationClass.IRRELEVANT), cls
    assert svc.promote(dull) is False
    assert svc._memory.facts == []
    print('TEST8 FULL PASS transient discarded', flush=True)


def test9_task_relevant_retention():
    from core.orchestrator.perception_service import ObservationClass
    svc = make_service()
    hot = obs(change='task_relevant', summary='save dialog open')
    cls, _ = svc.classify_observation(hot)
    assert cls is ObservationClass.TASK_RELEVANT, cls
    assert svc.promote(hot) is True, 'task-relevant not retained'
    assert any('a.exe' in f for _, f in svc._memory.facts), svc._memory.facts
    assert 'memory_write_ms' in svc.metrics.summary()
    print('TEST9 FULL PASS task-relevant retained', flush=True)


def test10_status_from_live_task():
    from core.orchestrator.realtime_voice import DeepResult
    c = make_controller(
        deep_handler=lambda t, ctx: DeepResult(
            speech_reply='Opening.', task_id='t-live', long_running=True),
        task_control=lambda a, *x, **k: 'live: filing into Reports')
    c.on_final_text('move the file into reports')
    c.wait_spoken()
    r = c.on_final_text('what are you doing')
    assert 'live: filing into Reports' in r, f'stale status: {r!r}'
    print('TEST10 FULL PASS live task status', flush=True)
    c.stop()


def test11_status_after_completion():
    c = make_controller()
    with c._lock:
        c._turn.active_task_id = 't-done'
        c._turn.task_context['goal'] = 'move the file'
    c.note_task_finished(True, 'The file was moved successfully.')
    r = c.on_final_text('what happened')
    assert 'moved successfully' in r and 'paused' not in r, repr(r)
    print('TEST11 FULL PASS completion status, no phantom pause',
          flush=True)
    c.stop()


def test12_interruption_merge():
    from core.orchestrator.realtime_voice import (
        VoiceState, FakeTTSBackend)
    c = make_controller(tts=FakeTTSBackend(word_ms=100.0))
    c._speak('I am checking the other account now and it takes a while '
             'to verify every single detail on the screen today.')
    time.sleep(0.4)
    assert c.state is VoiceState.SPEAKING, c.state
    c.on_final_text('no, use the third one')
    t = c.turn
    assert t.utterance_interrupted, 'interruption not recorded'
    assert t.task_context.get('correction', '').startswith('no, use'), \
        t.task_context
    assert t.interruption_epoch >= 1
    print('TEST12 FULL PASS correction merged, epoch kept', flush=True)
    c.stop()


def test13_no_duplicate_executor():
    import inspect
    from core.orchestrator import realtime_voice as rv_mod
    from core.orchestrator.realtime_voice import DeepTaskRunner
    src = inspect.getsource(rv_mod)
    assert 'execute_tool' not in src and 'run_powershell' not in src, \
        'voice grew its own executor'
    c = make_controller(
        deep_handler=lambda t, ctx: (time.sleep(4.0), None)[1] or
        __import__('core.orchestrator.realtime_voice',
                   fromlist=['DeepResult']).DeepResult(
                       speech_reply='x', task_id='t1', long_running=True))
    runner = DeepTaskRunner(c)
    runner.start('slow job')
    time.sleep(0.5)
    workers = [t for t in threading.enumerate()
               if t.name == 'dude-deep-task' and t.is_alive()]
    assert len(workers) == 1, f'{len(workers)} deep workers'
    c.stop()
    runner.wait(timeout=15)
    print('TEST13 FULL PASS one runner, no executor', flush=True)


def test14_resource_bounds():
    import psutil
    svc = make_service()
    with svc._lock:
        svc._last_observation = obs()
        svc._scene_version = 1
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    for _ in range(50):
        svc.get_scene()
    c = make_controller()
    for i in range(30):
        c._log_turn('user', f'q{i}')
    c.compact_history(keep_recent=10)
    growth_mb = (proc.memory_info().rss - rss0) / 1e6
    assert growth_mb < 50, f'growth {growth_mb:.0f}MB'
    assert len(svc.observations) <= 64 and len(c._history) <= 10
    assert svc._frames.live_count() == 0
    print(f'TEST14 FULL PASS growth={growth_mb:.1f}MB caches bounded',
          flush=True)
    c.stop()


def test15_stale_detection():
    svc = make_service()
    with svc._lock:
        svc._last_observation = obs(captured_at=time.time() - 30.0)
        svc._scene_version = 7
    s = svc.get_scene()
    assert s.is_stale() and s.freshness_ms() > 10000, s.freshness_ms()
    print('TEST15 FULL PASS stale scene flagged', flush=True)


def test16_freshness():
    svc = make_service()
    with svc._lock:
        svc._last_observation = obs()
        svc._scene_version = 3
    s = svc.get_scene()
    assert not s.is_stale() and s.freshness_ms() < 2000, s.freshness_ms()
    assert s.scene_version == 3
    print('TEST16 FULL PASS fresh scene, versioned', flush=True)


def main():
    test1_stable_backoff()
    test2_change_acceleration()
    test3_uia_change_detection()
    test4_screenshot_ttl()
    test5_screenshot_disposal()
    test6_semantic_scene_update()
    test7_secret_rejection()
    test8_transient_rejection()
    test9_task_relevant_retention()
    test10_status_from_live_task()
    test11_status_after_completion()
    test12_interruption_merge()
    test13_no_duplicate_executor()
    test14_resource_bounds()
    test15_stale_detection()
    test16_freshness()
    print('PHASE 12 SLICE: ALL 16 TESTS FULL PASS', flush=True)


if __name__ == '__main__':
    main()
