#!/usr/bin/env python
"""Phase 11 tests: realtime perception + voice loop (real desktop).

Covers the acceptance gaps beyond Phases 9/10: structured deltas,
no-duplicate-work, TTL screenshot lifecycle, secret redaction, partial
ASR, real TTS interrupt, interruption/correction preservation through
the concurrent DeepTaskRunner, responsiveness during deep work,
foreground-change safety, session compaction.
BANNED: run_powershell, write_file (outside temp), edge_tts, cloud.
"""
import os
import sys
import threading
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

BANNED_TOOLS = {"run_powershell", "write_file"}
FIXDIR = r'C:\Users\duvvu\AppData\Local\Temp\opencode\p10'
SPEECH_WAV = os.path.join(FIXDIR, 'fixture_open_notepad.wav')


class FakeMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


def windows_of(exe_name):
    import win32gui
    import win32process
    import psutil
    from core.orchestrator.app_instances import is_app_window
    out = set()

    def cb(h, acc):
        if not win32gui.IsWindowVisible(h):
            return True
        try:
            pid = win32process.GetWindowThreadProcessId(h)[1]
            exe = (psutil.Process(pid).name() or '').lower()
            if exe != exe_name:
                return True
            if is_app_window(exe, win32gui.GetClassName(h) or '',
                             win32gui.GetWindowText(h) or ''):
                acc.add(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, out)
    return out


def snapshot_desktop():
    return {
        'notepad.exe': windows_of('notepad.exe'),
        'explorer.exe': windows_of('explorer.exe'),
        'comet.exe': windows_of('comet.exe'),
    }


def build_perception():
    from core.screentree import ScreenMap
    from core.tools import init_screentree
    from core.memory import Memory
    from core.orchestrator import PerceptionEngine
    from core.orchestrator.perception_service import PerceptionService
    smap = ScreenMap()
    init_screentree(smap)
    time.sleep(3)
    assert smap.available(), 'UIA unavailable'

    def _capture_fn():
        try:
            from core.tools import _capture_screen_composite
            img, _ = _capture_screen_composite()
            return img
        except Exception:
            return None

    memory = Memory()
    perception = PerceptionEngine(
        get_screentree=lambda: smap, get_ocr_fn=lambda: None,
        get_capture_fn=_capture_fn)
    svc = PerceptionService(perception, capture_fn=_capture_fn,
                            memory=memory, fast_hz=10.0, idle_hz=4.0,
                            frame_ttl_seconds=4.0, max_frames=4)
    return svc, memory


def make_controller(**kw):
    from core.orchestrator.realtime_voice import (
        RealtimeInteractionController, FakeTTSBackend, FakeSTTBackend)
    kw.setdefault('tts', FakeTTSBackend(word_ms=20.0))
    kw.setdefault('stt', FakeSTTBackend(transcript='hello dude'))
    kw.setdefault('memory', FakeMemory())
    c = RealtimeInteractionController(**kw)
    c.start_listening()
    return c


def wait_for(pred, timeout, desc, poll=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(poll)
    raise AssertionError(f'timeout: {desc}')


def test1_structured_state(svc):
    base = wait_for(lambda: svc.current_observation(), 15,
                    'initial observation')
    for f in ('active_app', 'window_title', 'hwnd', 'dialog_kind',
              'change', 'confidence', 'source'):
        assert hasattr(base, f), f'missing field {f}'
    assert base.active_app and base.active_app != 'unknown'
    assert base.delta_summary(), 'no delta summary'
    print(f'TEST1 FULL PASS app={base.active_app!r} '
          f'delta={base.delta_summary()[:80]!r}', flush=True)


def test2_no_duplicate_heavy_work(svc):
    c0 = svc.counters()
    r0 = svc.metrics.summary().get('uia_refresh_ms', {}).get('count', 0)
    time.sleep(3.0)
    c1 = svc.counters()
    r1 = svc.metrics.summary().get('uia_refresh_ms', {}).get('count', 0)
    assert c1['redundant_ticks_avoided'] > c0['redundant_ticks_avoided'], \
        'idle ticks not counted as avoided'
    assert (r1 - r0) <= 3, f'heavy refresh ran {r1-r0}x during idle'
    print(f'TEST2 FULL PASS redundant+'
          f'{c1["redundant_ticks_avoided"]-c0["redundant_ticks_avoided"]} '
          f'refresh+{r1-r0}', flush=True)


def test3_scene_delta(svc, memory):
    from core.orchestrator.perception_service import (
        SemanticObservation, compute_scene_delta)
    a = SemanticObservation(active_app='comet.exe', window_title='w',
                            hwnd=1, focused_name='addr',
                            dialog_kind='none', change='low')
    b = SemanticObservation(active_app='comet.exe', window_title='w',
                            hwnd=1, focused_name='profile',
                            dialog_kind='menu', change='action_relevant',
                            related='window_appeared:comet.exe#2:m')
    d = compute_scene_delta(a, b)
    assert d.get('dialog_added') == 'menu', d
    assert d.get('focus_changed'), d
    assert d.get('popup_added'), d
    assert compute_scene_delta(None, a).get('initial'), 'no initial delta'
    assert compute_scene_delta(a, a) == {}, 'noise reported as delta'
    # Live: observation log holds structured entries with deltas.
    obs = wait_for(lambda: svc.current_observation(), 10, 'live obs')
    assert isinstance(obs.delta, dict), 'live obs has no delta dict'
    assert len(svc.observations) >= 1, 'observation log empty'
    print(f'TEST3 FULL PASS unit deltas + live delta keys='
          f'{sorted(obs.delta)}', flush=True)


def test4_screenshot_discarded(svc):
    data = svc.capture_frame('p11test')
    assert data, 'no frame captured'
    assert svc._frames.live_count() >= 1
    time.sleep(5.5)
    assert svc._frames.sweep() >= 1 or svc._frames.live_count() == 0
    assert svc._frames.live_count() == 0, 'frame survived TTL'
    assert svc.counters()['expired_items_deleted'] >= 1
    print('TEST4 FULL PASS frame expired, counters updated', flush=True)


def test5_secrets_not_persisted(svc, memory):
    from core.orchestrator.perception_service import SemanticObservation
    marker = f'p11probe{time.time_ns() % 1000000}'
    probe = SemanticObservation(
        captured_at=time.time(), active_app='probe',
        window_title=f'{marker} login', dialog_kind='none', change='low',
        summary=f'{marker} api_key=SECRET123 shown on screen',
        related='dialog_added=login', delta={'dialog_added': 'login'})
    assert svc.remember_observation(probe, category='screen_state_p11test')
    rows = memory.facts_by_category('screen_state_p11test', limit=20)
    joined = ' '.join(r.get('fact', '') for r in rows)
    assert marker in joined, 'probe fact missing'
    assert 'SECRET123' not in joined, f'SECRET PERSISTED: {joined[:200]}'
    assert '<REDACTED' in joined, 'no redaction marker'
    print('TEST5 FULL PASS redacted + retrievable', flush=True)


def _pcm_mono_f32(path, target_sr=16000):
    import numpy as np
    import soundfile as sf
    audio, sr = sf.read(path, dtype='float32')
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != target_sr:
        idx = (np.arange(int(len(audio) * target_sr / sr))
               * (sr / target_sr)).astype(int)
        idx = np.clip(idx, 0, len(audio) - 1)
        audio = audio[idx]
    return np.asarray(audio, dtype=np.float32)


def test6_partial_asr():
    from core.orchestrator.realtime_voice import (
        FakeSTTBackend, TinyWhisperBackend)
    c = make_controller(stt=FakeSTTBackend('open the pod bay doors'))
    pcm = _pcm_mono_f32(SPEECH_WAV)
    part = c.ingest_partial(pcm[:len(pcm) * 3 // 4], 16000)
    assert part and part != 'open the pod bay doors', \
        f'partial should be unstable subset: {part!r}'
    assert c.turn.partial_text == part, 'partial not stashed'
    assert 'stt_first_partial_ms' in c.metrics.summary(), 'no partial metric'
    assert not c.tts.spoken, 'partial was routed as intent!'
    # Real local partial path (unstable by contract).
    real = TinyWhisperBackend('tiny.en')
    text, ms, stable = real.partial(pcm[:len(pcm) * 3 // 4], 16000)
    real.close()
    assert stable is False and isinstance(text, str), 'partial contract'
    print(f'TEST6 FULL PASS fake={part!r} real={text!r} ms={ms:.0f}',
          flush=True)
    c.stop()


def test7_tts_interruptible_real():
    from core.orchestrator.realtime_voice import SapiTTSBackend
    be = SapiTTSBackend()
    import threading
    done = []
    th = threading.Thread(
        target=lambda: done.append(be.speak(
            'This is sentence one. This is sentence two. '
            'This is sentence three. This is sentence four.')),
        daemon=True)
    th.start()
    time.sleep(1.0)
    assert be.is_speaking(), 'real TTS never started speaking'
    ms = be.stop()
    th.join(timeout=10)
    assert not th.is_alive(), 'speak thread hung after stop'
    assert be.interrupted, 'interrupt flag not set'
    # A deliberate stop is an abort, not a failure: no retry storm,
    # no false "suspicious" count.
    assert done == [False], f'aborted speak should return False: {done}'
    assert be.suspicious_completions == 0, 'deliberate stop miscounted'
    # Engine must still work afterwards (post-stop rebuild).
    assert be.synth_to_file('Short check.', os.path.join(
        FIXDIR, 'interrupt_after.wav')), 'post-stop synth dead'
    print(f'TEST7 FULL PASS real SAPI interrupted stop_ms={ms:.1f}',
          flush=True)


def test8_interruption_preserves_runner_task():
    from core.orchestrator.realtime_voice import (
        DeepTaskRunner, DeepResult, VoiceState)

    def slow_deep(text, ctx):
        time.sleep(6.0)
        return DeepResult(speech_reply='Deep update.',
                          task_id='t-slow', long_running=True)

    c = make_controller(deep_handler=slow_deep)
    runner = DeepTaskRunner(c)
    tid = runner.start('switch to the other account',
                       preamble='On it.')
    assert runner.alive(), 'runner thread never started'
    c.wait_spoken()
    assert c.state is VoiceState.EXECUTING_TASK, c.state
    # Interrupt mid-task like a barge-in would.
    c.on_final_text('actually use the third one')
    t = c.turn
    assert t.active_task_id in (tid, 't-slow'), \
        f'task destroyed: {t.active_task_id}'
    assert t.task_context.get('goal') == 'switch to the other account', \
        f'goal clobbered: {t.task_context}'
    assert 'third' in t.task_context.get('correction', ''), \
        'correction not merged'
    c.stop()
    runner.wait(timeout=15)
    print('TEST8 FULL PASS runner task survives interruption', flush=True)


def test9_correction_not_new_task():
    from core.orchestrator.realtime_voice import DeepTaskRunner, DeepResult
    calls = []

    def deep(text, ctx):
        calls.append(text)
        time.sleep(3.0)
        return DeepResult(speech_reply='Working.', task_id='t-acc',
                          long_running=True)

    c = make_controller(deep_handler=deep)
    controlled = []
    c.task_control = lambda a, *x, **k: (
        controlled.append(a), 'replanned')[1]
    runner = DeepTaskRunner(c)
    tid = runner.start('switch comet to the other account')
    c.wait_spoken()
    n_threads_before = threading.active_count()
    c.on_final_text('actually the third account')
    assert runner.task_id == tid, 'correction spawned a new task'
    assert threading.active_count() <= n_threads_before + 1, \
        'extra worker threads spawned'
    assert c.turn.task_context.get('goal') == \
        'switch comet to the other account', 'goal replaced'
    merged = c.merge_correction_and_replan()
    assert controlled == ['replan'] and merged == 'replanned', \
        f'replan not invoked: {controlled} {merged}'
    c.stop()
    runner.wait(timeout=15)
    print('TEST9 FULL PASS corrected in place, replanned', flush=True)


def test10_responsive_during_deep():
    from core.orchestrator.realtime_voice import DeepTaskRunner, DeepResult
    c = make_controller(
        deep_handler=lambda t, ctx: (
            time.sleep(8.0), DeepResult(
                speech_reply='Done deep work.', task_id='t-deep',
                long_running=True))[1])
    runner = DeepTaskRunner(c)
    runner.start('do eight seconds of deep work')
    time.sleep(1.0)
    assert runner.alive(), 'deep task not running'
    t0 = time.time()
    reply = c.on_final_text('what time is it')
    dt = time.time() - t0
    assert ':' in reply and dt < 3.0, f'slow/blocked: {reply!r} {dt:.1f}s'
    assert runner.alive(), 'fast path killed the deep task'
    c.stop()
    runner.wait(timeout=15)
    print(f'TEST10 FULL PASS fast reply in {dt:.2f}s during deep work',
          flush=True)


def test11_foreground_change_safety(before):
    import win32gui
    from core.tools import bring_to_foreground, open_app
    from core.orchestrator.realtime_voice import DeepTaskRunner, DeepResult
    from core.memory import Memory
    mem = Memory()
    owned = []
    c = None
    try:
        for _ in range(2):
            r = open_app(mem, {'name': 'Notepad', 'new': True})
            assert not str(r).startswith('ERROR'), f'open failed: {r}'
            time.sleep(2.0)
        owned = sorted(windows_of('notepad.exe') - before['notepad.exe'])
        assert len(owned) >= 2, 'need two test-owned windows'
        task_hwnd, other_hwnd = owned[:2]

        c = make_controller()

        def owned_window_task(text, ctx):
            for _ in range(60):
                if ctx.get('should_stop') and ctx['should_stop']():
                    return DeepResult(speech_reply='Paused safely.',
                                      task_id='t-fg',
                                      long_running=True)
                time.sleep(0.2)
            return DeepResult(speech_reply='Task window work done.',
                              task_id='t-fg', long_running=True)

        c.deep_handler = owned_window_task
        runner = DeepTaskRunner(c)
        runner.start('work in task-owned window')
        c.wait_spoken()
        # User drives foreground elsewhere: task window must survive.
        assert bring_to_foreground(other_hwnd), 'could not fg other window'
        time.sleep(1.5)
        assert bring_to_foreground(task_hwnd), 'could not fg task window'
        time.sleep(1.5)
        assert win32gui.IsWindow(task_hwnd), 'TASK-OWNED window died'
        assert win32gui.IsWindow(other_hwnd), 'other test window died'
        assert runner.alive(), 'deep task died on fg switch'
        print('TEST11 FULL PASS fg switches safe, task alive', flush=True)
    finally:
        if c is not None:
            try:
                c.stop()
            except Exception:
                pass
        for h in owned:
            try:
                if win32gui.IsWindow(h):
                    win32gui.PostMessage(h, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(1.0)
        leftovers = windows_of('notepad.exe') - before['notepad.exe']
        assert not leftovers, f'test-owned leftovers: {leftovers}'


def test12_compaction_preserves_task():
    c = make_controller()
    for i in range(40):
        c._log_turn('user', f'filler question number {i} about weather')
        c._log_turn('assistant', f'filler answer number {i}')
    c._record_fact('task goal: switch comet to the other account')
    c._record_fact('correction: actually the third one')
    with c._lock:
        c._turn.task_context['goal'] = 'switch comet to the other account'
        c._turn.task_context['correction'] = 'actually the third one'
        c._turn.active_task_id = 't-keep'
    out = c.compact_history(keep_recent=10)
    assert out['dropped_turns'] == 70, f'dropped={out["dropped_turns"]}'
    assert len(c._history) <= 10, 'verbatim unbounded'
    assert any('other account' in f for f in out['facts']), 'goal lost'
    assert any('third one' in f for f in out['facts']), 'correction lost'
    assert out['active_task'] == 't-keep', 'active task lost'
    assert c.turn.task_context.get('goal'), 'task params destroyed'
    sys_msgs = [m for r, m in c.memory.messages if r == 'system']
    assert sys_msgs, 'no compaction summary persisted'
    print(f'TEST12 FULL PASS dropped={out["dropped_turns"]} '
          f'facts={len(out["facts"])} task intact', flush=True)
    c.stop()


def main():
    before = snapshot_desktop()
    svc, memory = build_perception()
    svc.start()
    try:
        test1_structured_state(svc)
        test2_no_duplicate_heavy_work(svc)
        test3_scene_delta(svc, memory)
        test4_screenshot_discarded(svc)
        test5_secrets_not_persisted(svc, memory)
        test6_partial_asr()
        test7_tts_interruptible_real()
        test8_interruption_preserves_runner_task()
        test9_correction_not_new_task()
        test10_responsive_during_deep()
        test11_foreground_change_safety(before)
        test12_compaction_preserves_task()
    finally:
        try:
            svc.stop()
        except Exception:
            pass
        after = snapshot_desktop()
        for k, v in before.items():
            assert not (v - after[k]), f'PRE-EXISTING {k} DIED'
        print('CLEANUP OK pre-existing alive, no leftovers', flush=True)
    print('PHASE 11: ALL 12 TESTS FULL PASS', flush=True)


if __name__ == '__main__':
    main()
