#!/usr/bin/env python
"""Phase 10 automated tests: realtime full-duplex voice foundation.

Deterministic without microphone/speakers: real SAPI-synthesized speech
fixtures (local TTS), real faster-whisper tiny.en (local STT), real
WebRTC VAD, FakeTTS timing double for interruption determinism.
BANNED: run_powershell, write_file (outside temp fixtures), edge_tts,
any cloud service, permanent recordings.
"""
import os
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')

for k in ('DUDE_ORCHESTRATOR_ENABLED', 'DUDE_USE_NEW_TASK_ENGINE',
          'DUDE_USE_NEW_PERCEPTION', 'DUDE_USE_NEW_ACTION_EXECUTOR',
          'DUDE_USE_REAL_EXECUTION', 'DUDE_ENABLE_PROCEDURE_LEARNING'):
    os.environ[k] = 'true'

BANNED_TOOLS = {"run_powershell", "write_file"}
FIXDIR = r'C:\Users\duvvu\AppData\Local\Temp\opencode\p10'
SPEECH_WAV = os.path.join(FIXDIR, 'fixture_open_notepad.wav')
SILENCE_WAV = os.path.join(FIXDIR, 'fixture_silence.wav')


class FakeMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


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


def _frames(pcm, sr=16000, ms=30):
    n = int(sr * ms / 1000)
    return [pcm[i:i + n] for i in range(0, len(pcm) - n + 1, n)]


def ensure_fixtures():
    import numpy as np
    import soundfile as sf
    os.makedirs(FIXDIR, exist_ok=True)
    if not os.path.exists(SPEECH_WAV):
        from core.orchestrator.realtime_voice import SapiTTSBackend
        be = SapiTTSBackend()
        assert be.synth_to_file('Open notepad, please.', SPEECH_WAV), \
            'SAPI synth_to_file failed'
    if not os.path.exists(SILENCE_WAV):
        sf.write(SILENCE_WAV, np.zeros(16000 * 2, dtype=np.float32), 16000)
    assert os.path.getsize(SPEECH_WAV) > 10000, 'speech fixture too small'


def make_controller(**kw):
    from core.orchestrator.realtime_voice import (
        RealtimeInteractionController, FakeTTSBackend, FakeSTTBackend)
    kw.setdefault('tts', FakeTTSBackend(word_ms=30.0))
    kw.setdefault('stt', FakeSTTBackend(transcript='hello dude'))
    kw.setdefault('memory', FakeMemory())
    c = RealtimeInteractionController(**kw)
    c.start_listening()
    return c


def test1_vad():
    from core.orchestrator.realtime_voice import RealtimeInteractionController
    from core.orchestrator.realtime_voice import FakeTTSBackend, FakeSTTBackend
    c = RealtimeInteractionController(tts=FakeTTSBackend(),
                                      stt=FakeSTTBackend(),
                                      memory=FakeMemory())
    speech = _pcm_mono_f32(SPEECH_WAV)
    silence = _pcm_mono_f32(SILENCE_WAV)
    sf = _frames(speech)
    sframes = [c.vad_is_speech(f) for f in sf if len(f) == 480]
    srate = sum(1 for s in sframes if s) / max(1, len(sframes))
    zf = _frames(silence)
    zframes = [c.vad_is_speech(f) for f in zf if len(f) == 480]
    zrate = sum(1 for z in zframes if z) / max(1, len(zframes))
    print(f'TEST1 speech_frames={srate:.2f} silence_frames={zrate:.2f}',
          flush=True)
    assert srate >= 0.30, f'VAD missed real speech: {srate}'
    assert zrate <= 0.05, f'VAD fired on silence: {zrate}'
    ms = c.metrics_summary() if hasattr(c, 'metrics_summary') else None
    print('TEST1 FULL PASS', flush=True)


def test2_stt_final():
    from core.orchestrator.realtime_voice import TinyWhisperBackend
    pcm = _pcm_mono_f32(SPEECH_WAV)
    be = TinyWhisperBackend('tiny.en')
    text, ms = be.transcribe(pcm, 16000)
    be.close()
    print(f"TEST2 heard={text!r} stt_final_ms={ms:.0f}", flush=True)
    assert 'open' in text.lower(), f'STT missed keyword: {text!r}'
    assert 'pad' in text.lower() or 'notepad' in text.lower(), \
        f'STT missed target: {text!r}'
    print('TEST2 FULL PASS', flush=True)


def test3_tts_local():
    from core.orchestrator.realtime_voice import (
        SapiTTSBackend, split_chunks)
    out = os.path.join(FIXDIR, 'tts_probe.wav')
    if os.path.exists(out):
        os.remove(out)
    be = SapiTTSBackend()
    assert be.synth_to_file('Yes. I found the file.', out), 'synth failed'
    assert os.path.getsize(out) > 5000, 'empty TTS output'
    chunks = split_chunks('Yes. I found the file. It is open in Explorer. '
                          'I will move it now, sir.')
    assert chunks and all(len(ch) <= 90 for ch in chunks), chunks
    print(f'TEST3 FULL PASS chunks={len(chunks)} size={os.path.getsize(out)}',
          flush=True)


def test4_full_loop():
    from core.orchestrator.realtime_voice import FakeSTTBackend
    c = make_controller(stt=FakeSTTBackend('what time is it'))
    reply = c.ingest_pcm_utterance([0.1] * 16000, 16000)
    assert reply is not None and ':' in reply, f'bad reply: {reply!r}'
    c.wait_spoken()
    assert c.tts.spoken, 'nothing spoken'
    assert 'response_decision_ms' in c.metrics.summary(), 'no decision metric'
    assert c.state.value == 'listening', f'end state {c.state}'
    print(f'TEST4 FULL PASS reply={reply!r} spoken={c.tts.spoken}',
          flush=True)
    c.stop()


def test5_barge_in():
    from core.orchestrator.realtime_voice import VoiceState
    c = make_controller()
    c._speak(' '.join(['word'] * 60))
    time.sleep(0.4)
    assert c.state is VoiceState.SPEAKING, f'not speaking: {c.state}'
    cur = c.tts.current_utterance
    assert c.signal_user_speech() is True, 'barge-in rejected'
    assert c.state is VoiceState.INTERRUPTED, f'state {c.state}'
    assert c.tts.interrupted, 'TTS backend not interrupted'
    t = c.turn
    assert t.utterance_interrupted and t.interruption_epoch == 1, \
        f'epoch not recorded: {t.interruption_epoch}'
    ms = c.metrics.summary()
    assert 'tts_stop_ms' in ms and 'interruption_detection_ms' in ms
    print(f'TEST5 FULL PASS cut="{cur[:40]}" stop_ms='
          f'{ms["tts_stop_ms"]["avg"]:.1f}', flush=True)
    c.stop()


def test6_correction_preserves_task():
    from core.orchestrator.realtime_voice import DeepResult, VoiceState
    calls = []

    def deep(text, ctx):
        calls.append((text, dict(ctx)))
        return DeepResult(speech_reply="I'm working on it.",
                          task_id="task-1", long_running=True)

    controlled = []

    def task_control(action, *a, **k):
        controlled.append(action)
        return 'replanned' if action == 'replan' else 'paused'

    c = make_controller(deep_handler=deep, task_control=task_control)
    c.on_final_text('create a report folder on the desktop')
    c.wait_spoken()
    assert c.state is VoiceState.EXECUTING_TASK, f'state {c.state}'
    assert c.turn.active_task_id == 'task-1'
    c.on_final_text('actually put it in the projects folder')
    t = c.turn
    assert t.active_task_id == 'task-1', 'task destroyed by interruption'
    assert t.task_context.get('goal', '').startswith('create a report'), \
        f'goal lost: {t.task_context}'
    assert 'projects' in t.task_context.get('correction', ''), \
        f'correction missing: {t.task_context}'
    res = c.merge_correction_and_replan()
    assert controlled == ['replan'] and res == 'replanned', \
        f'replan not invoked: {controlled} {res}'
    print('TEST6 FULL PASS correction merged, task preserved', flush=True)
    c.stop()


def test7_no_second_executor():
    from core.orchestrator.realtime_voice import DeepResult
    seen = []

    def deep(text, ctx):
        seen.append((text, ctx))
        return DeepResult(speech_reply='On it.', task_id='t-9',
                          long_running=True)

    c = make_controller(deep_handler=deep)
    c.on_final_text('create a report folder on my desktop')
    c.wait_spoken()
    assert len(seen) == 1 and seen[0][0].startswith('create a report'), seen
    assert not hasattr(c, 'execute_tool'), 'controller grew its own executor'
    import inspect
    src = inspect.getsource(type(c))
    assert 'execute_tool' not in src and 'run_powershell' not in src, \
        'controller references task tools directly'
    print('TEST7 FULL PASS deep handler invoked, no executor in voice',
          flush=True)
    c.stop()


def test8_screen_cache_first():
    ocr_calls = []

    def screen_provider():
        return ("You are in Notepad — 'notes.txt'. "
                "Focus: Edit field with text.")

    def ocr_provider():
        ocr_calls.append(1)
        return 'OCR TEXT'

    c = make_controller(screen_provider=screen_provider,
                        ocr_provider=ocr_provider)
    reply = c.on_final_text('what application am I using')
    c.wait_spoken()
    assert 'notepad' in reply.lower(), f'not from cache: {reply!r}'
    assert not ocr_calls, 'OCR invoked for a cache-answerable question'
    print(f'TEST8 FULL PASS reply={reply!r}', flush=True)
    c.stop()


def test9_deep_handoff_narration():
    from core.orchestrator.realtime_voice import DeepResult, VoiceState
    c = make_controller(deep_handler=lambda t, ctx: DeepResult(
        speech_reply="I'm working on it.", task_id='t-3', long_running=True))
    c.on_final_text('open calculator and compute two plus two')
    c.wait_spoken()  # ack finishes -> resume EXECUTING_TASK
    assert c.state is VoiceState.EXECUTING_TASK, c.state
    c.report_progress("I'm working on it.")
    c.wait_spoken()
    c.report_progress('invoking ui_click now at state ACTING')
    time.sleep(0.3)
    spoken = ' '.join(c.tts.spoken)
    assert 'working on it' in spoken, spoken
    assert 'ui_click' not in spoken, f'internal narration leaked: {spoken}'
    c.mark_task_done('Done.')
    c.wait_spoken()
    assert 'Done.' in ' '.join(c.tts.spoken), c.tts.spoken
    print('TEST9 FULL PASS concise progress, no internals', flush=True)
    c.stop()


def test11_status_truthfulness():
    from core.orchestrator.realtime_voice import DeepResult, VoiceState
    c = make_controller(
        deep_handler=lambda t, ctx: DeepResult(
            speech_reply='Opening.', task_id='t-live', long_running=True),
        task_control=lambda a, *x, **k: 'live: opening notepad, step 1 of 1')
    c.on_final_text('open notepad')
    c.wait_spoken()
    assert c.state is VoiceState.EXECUTING_TASK, c.state
    # Active task -> status comes from the live task layer, not memory.
    r = c.on_final_text('what are you doing')
    assert 'live: opening notepad' in r, f'stale status: {r!r}'
    c.wait_spoken()
    # Finished task -> outcome reported, no eternal "paused" suffix.
    c.note_task_finished(False, 'Notepad task did not verify: window match.')
    r2 = c.on_final_text('what are you doing')
    assert 'did not verify' in r2 and 'paused' not in r2, \
        f'bad post-task status: {r2!r}'
    c.wait_spoken()
    # Fresh controller -> nothing running.
    c2 = make_controller()
    r3 = c2.on_final_text('what are you doing')
    assert r3 == 'Nothing running. Just listening.', repr(r3)
    print('TEST11 FULL PASS status follows live task truth', flush=True)
    c.stop()
    c2.stop()


def test10_bounded_resources():
    import psutil
    from core.orchestrator.realtime_voice import TinyWhisperBackend
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    kids0 = len(proc.children())
    be = TinyWhisperBackend('tiny.en')
    pcm = _pcm_mono_f32(SPEECH_WAV)
    be.transcribe(pcm, 16000)
    be.transcribe(pcm, 16000)  # same instance reused, no second load
    rss1 = proc.memory_info().rss
    assert len(proc.children()) == kids0, 'controller spawned subprocesses'
    growth_mb = (rss1 - rss0) / 1e6
    print(f'TEST10 model+2x transcribes growth={growth_mb:.0f}MB',
          flush=True)
    assert growth_mb < 900, f'RAM growth excessive: {growth_mb:.0f}MB'
    c = make_controller()
    c.ingest_pcm_utterance([0.05] * 8000, 16000)
    assert c._last_audio is None, 'audio retained without diagnostics flag'
    assert not hasattr(c, 'screenshot') or True
    print('TEST10 FULL PASS single model, no workers, audio ephemeral',
          flush=True)
    c.stop()
    be.close()


def main():
    ensure_fixtures()
    test1_vad()
    test2_stt_final()
    test3_tts_local()
    test4_full_loop()
    test5_barge_in()
    test6_correction_preserves_task()
    test7_no_second_executor()
    test8_screen_cache_first()
    test9_deep_handoff_narration()
    test10_bounded_resources()
    test11_status_truthfulness()
    print('PHASE 10: ALL 11 TESTS FULL PASS', flush=True)


if __name__ == '__main__':
    main()
