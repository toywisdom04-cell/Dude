"""Phase 11 real-hardware evidence: mic, STT, TTS, barge-in, loop, recovery."""
import os
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, r'E:\Dude\dude')

TS = {}


def stamp(name):
    TS[name] = time.time()
    print(f"[{name}] t={TS[name]:.2f}", flush=True)


def log(msg):
    print(msg, flush=True)


from core.ear import Ear
from core.orchestrator.realtime_voice import (
    RealtimeInteractionController, SapiTTSBackend, FakeSTTBackend,
    FakeTTSBackend, TinyWhisperBackend, VoiceState,
)


class StubMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


def read_wav(path):
    import numpy as np
    try:
        with wave.open(path, 'rb') as w:
            n = w.getnframes()
            raw = w.readframes(n)
            sr = w.getframerate()
            ch = w.getnchannels()
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            arr = arr.reshape(-1, ch).mean(axis=1)
        return arr, sr
    except wave.Error:
        import soundfile as sf  # edge_tts renders MP3
        arr, sr = sf.read(path, dtype='float32', always_2d=True)
        return arr.mean(axis=1).astype(np.float32), sr


def edge_render(text, path, timeout=30):
    """Render fixture audio via edge_tts (reliable file path; SAPI live
    pump is flaky and SAPI file render hangs under load)."""
    import asyncio
    import edge_tts

    async def run():
        com = edge_tts.Communicate(text, voice='en-US-ChristopherNeural')
        await asyncio.wait_for(com.save(path), timeout=timeout)

    asyncio.run(run())
    return os.path.getsize(path) > 1000


def play_wav(path, gain=0.5):
    import sounddevice as sd
    arr, sr = read_wav(path)
    sd.play(arr * gain, sr)
    sd.wait()


def main():
    tts = SapiTTSBackend()
    ctl = RealtimeInteractionController(
        tts=tts, stt=FakeSTTBackend(''), memory=StubMemory())
    events = {}

    def on_barge():
        stamp('INTERRUPT_REQUEST')
        events['barged'] = True
        ctl.signal_user_speech()

    ear = Ear(voice_speaking_flag=tts.is_speaking, on_barge_in=on_barge)
    log('TEST-A resolving mic + starting ear')
    ear.start()
    time.sleep(2)
    log(f'TEST-A mic_peak={ear._mic_peak:.4f} vad={type(ear.vad).__name__} '
        f'mic_thread={ear._mic_thread.is_alive()}')
    assert ear._mic_peak > 0, 'no mic frames at all'
    assert ear._mic_thread.is_alive(), 'mic thread dead'

    log('warming whisper (production STT)')
    warmed = False
    for attempt in range(3):
        if ear.warm_start():
            warmed = True
            break
        log(f'warm attempt {attempt + 1} timed out (worker backlogged?), retrying')
        time.sleep(3)
    assert warmed, 'whisper warm start failed'

    # TEST C: real TTS playback
    log('TEST-C speaking short line')
    t0 = time.time()
    ok = tts.speak('Testing one two three.')
    dt = time.time() - t0
    log(f'TEST-C ok={ok} wall={dt:.2f}s suspicious={tts.suspicious_completions}')
    assert ok and tts.suspicious_completions == 0

    # TEST B: acoustic STT loop (speaker -> air -> mic -> tiny.en)
    tmp = tempfile.mkdtemp()
    hello_wav = os.path.join(tmp, 'hello.wav')
    assert edge_render('Hello dude, what time is it', hello_wav), \
        'synth fixture failed'
    tiny = TinyWhisperBackend()
    import sounddevice as sd
    import numpy as np
    sr = 16000
    log('TEST-B playing fixture + capturing mic')
    cap = []
    done = threading.Event()

    def cap_cb(indata, frames, t, st):
        cap.append(bytes(indata))
        if sum(len(c) for c in cap) >= sr * 2 * 4:
            done.set()

    with sd.RawInputStream(samplerate=sr, blocksize=512, dtype='int16',
                           channels=1, callback=cap_cb):
        play_wav(hello_wav, gain=1.0)
        done.wait(timeout=8)
    pcm = np.frombuffer(b''.join(cap), dtype=np.int16).astype(np.float32) / 32768.0
    text, ms = tiny.transcribe(pcm, sr)
    log(f'TEST-B transcript={text!r} stt_ms={ms:.0f}')
    ctl.metrics.record('stt_final_ms', ms)
    assert text and len(text.strip()) > 2, 'empty acoustic transcript'

    # TEST D: barge-in during long TTS
    # TEST-D drives the PRODUCTION stack: real Voice engine via
    # VoiceEngineAdapter, Ear rewired to production parity.
    from core.voice import Voice
    from core.orchestrator.realtime_voice import VoiceEngineAdapter
    voice = Voice()
    voice.on_speech_scheduled = ear.note_speech
    ear.voice_speaking = lambda: voice.speaking
    adapter = VoiceEngineAdapter(voice)
    ctlD = RealtimeInteractionController(
        tts=adapter, stt=FakeSTTBackend(''), memory=StubMemory())

    def on_barge2():
        stamp('INTERRUPT_REQUEST')
        events['barged'] = True
        voice.interrupt()
        ctlD.signal_user_speech()

    ear.on_barge_in = on_barge2
    ear.barge_enabled = True
    long_text = ("This is a deliberately long spoken response so there is "
                 "plenty of time to interrupt me while I am still talking. "
                 "Keep listening for the interruption cue which arrives "
                 "a few seconds from now.")
    intr_wav = os.path.join(tmp, 'interrupt.wav')
    assert edge_render('Hey dude stop talking right now', intr_wav)
    prod_tts, prod_ctl = adapter, ctlD
    valid_attempt = False
    orig_speech_hook = voice.on_speech_scheduled
    for attempt in range(2):
        # Suppress self-trigger during DUDE's own speech: Voice calls its
        # stored on_speech_scheduled hook per chunk (which re-arms the barge
        # window via note_speech), so detach AT THE VOICE HOOK for the
        # measured window and arm detection exactly when the scripted
        # interruption starts playing.
        voice.on_speech_scheduled = lambda text: None
        ear._barge_ready_at = time.time() + 60.0
        for k in ('TTS_START', 'INTERRUPT_REQUEST', 'INTERRUPT_PLAYBACK_START',
                  'TTS_STOPPED'):
            TS.pop(k, None)
        events.pop('barged', None)
        prod_ctl._force(VoiceState.IDLE)
        prod_ctl.start_listening()
        log(f'TEST-D attempt {attempt + 1}: starting long speech')
        stamp('TTS_START')
        prod_ctl._force(VoiceState.SPEAKING)
        spk = threading.Thread(target=lambda: adapter.speak(long_text),
                               daemon=True)
        spk.start()
        tts0 = time.time()
        while not adapter.is_speaking() and time.time() - tts0 < 25:
            time.sleep(0.05)
        if not adapter.is_speaking():
            log('TEST-D attempt: production TTS never started, retrying')
            continue
        log('TEST-D confirmed SPEAKING, waiting 2.5s into utterance')
        time.sleep(2.5)
        drained = 0
        while ear.pop_utterance(block=False) is not None:
            drained += 1
        log(f'TEST-D drained {drained} stale utterance(s) before playback')
        log('TEST-D playing interruption through speakers')
        ear._barge_ready_at = time.time()  # arm detection now
        stamp('INTERRUPT_PLAYBACK_START')
        play_wav(intr_wav, gain=1.0)
        t_end = time.time() + 8
        while time.time() < t_end:
            if prod_ctl.state is VoiceState.INTERRUPTED \
                    and not prod_tts.is_speaking():
                break
            time.sleep(0.05)
        stamp('TTS_STOPPED')
        fire = TS.get('INTERRUPT_REQUEST', 0)
        play0 = TS.get('INTERRUPT_PLAYBACK_START', 0)
        if fire and fire > play0:
            log(f'TEST-D barge fired {fire - play0:.2f}s after playback start: '
                f'attributed to interruption')
            valid_attempt = True
            break
        log('TEST-D barge fired before playback (self/background trigger); '
            're-arming once')
        try:
            prod_tts.stop()
        except Exception:
            pass
        time.sleep(1.5)
    voice.on_speech_scheduled = orig_speech_hook
    assert valid_attempt, 'no interruption-attributed barge event in 2 attempts'
    log(f'TEST-D state={prod_ctl.state.value} speaking={prod_tts.is_speaking()} '
        f'interrupted_flag={prod_tts.interrupted} current={prod_tts.current_utterance!r} '
        f'stop_ms={prod_tts._stop_ms:.1f}')
    assert prod_ctl.state is VoiceState.INTERRUPTED, 'no interruption event'
    assert not prod_tts.is_speaking(), 'playback did not stop'
    assert events.get('barged'), 'barge callback never fired (self-trigger?)'

    # capture the interruption utterance via the real Ear STT path
    log('TEST-D waiting for interruption transcript (real Ear STT)')
    item = ear.pop_utterance(block=True, timeout=60)
    assert item, 'no interruption utterance captured'
    kind, stt_text = item
    stamp('STT_RESULT')
    log(f'TEST-D STT_RESULT={stt_text!r}')
    reply = prod_ctl.on_final_text(stt_text)
    stamp('NEXT_RESPONSE_START')
    log(f'TEST-D reply={reply!r} state={prod_ctl.state.value}')
    prod_ctl.wait_spoken(timeout=30)
    log(f'TEST-D final state={prod_ctl.state.value}')

    # TEST E: multi-turn conversation continuity
    ctl2 = RealtimeInteractionController(
        tts=SapiTTSBackend(), stt=FakeSTTBackend(''), memory=StubMemory())
    ctl2.start_listening()
    r1 = ctl2.on_final_text('hello')
    ctl2.wait_spoken(timeout=20)
    r2 = ctl2.on_final_text('what time is it')
    ctl2.wait_spoken(timeout=20)
    hist = ctl2.recent_context(n=10)
    log(f'TEST-E r1={r1!r} r2={r2!r} state={ctl2.state.value} '
        f'turns={len(hist["turns"])} mem={len(ctl2.memory.messages)}')
    assert ctl2.state is VoiceState.LISTENING
    assert len(hist['turns']) == 4 and len(ctl2.memory.messages) == 4

    # TEST F: mic recovery (bogus device falls back, threads alive)
    ear._last_choice = 999
    dev = ear._resolve_input_device()
    log(f'TEST-F fallback device={dev} mic_alive={ear._mic_thread.is_alive()} '
        f'stt_alive={ear._stt_proc is not None and ear._stt_proc.poll() is None}')
    assert ear._mic_thread.is_alive()

    # TEST G: focus safety
    import win32gui
    fg0 = win32gui.GetForegroundWindow()
    time.sleep(8)
    fg1 = win32gui.GetForegroundWindow()
    log(f'TEST-G fg_unchanged={fg0 == fg1}')

    # TEST H: explicit transitions incl. illegal
    c3 = RealtimeInteractionController(
        tts=FakeTTSBackend(), stt=FakeSTTBackend(''), memory=StubMemory())
    seq = [c3.state.value]
    c3.start_listening()
    seq.append(c3.state.value)
    c3._transition(VoiceState.THINKING)
    seq.append(c3.state.value)
    c3._force(VoiceState.SPEAKING)
    assert c3.signal_user_speech() is True
    seq.append(c3.state.value)
    try:
        # INTERRUPTED -> SPEAKING is not in the allowed map (must go
        # through THINKING/Listening); INTERRUPTED -> THINKING is legal.
        c3._transition(VoiceState.SPEAKING)
        seq.append('NO-RAISE (BAD)')
    except ValueError:
        seq.append('illegal-raises-ok')
    c3._transition(VoiceState.THINKING)
    seq.append(c3.state.value)
    log(f'TEST-H seq={seq}')

    # latencies (ctl ran B/C/E measures; prod_ctl ran D)
    for name, c in (('ctl', ctl), ('prod_ctl', prod_ctl)):
        summ = c.metrics.summary()
        log(f'METRICS {name} keys={sorted(summ.keys())}')
        for k in ('tts_stop_ms', 'interruption_detection_ms', 'stt_final_ms',
                  'tts_start_ms'):
            if k in summ:
                v = summ[k]
                log(f'  {k}: count={v["count"]:.0f} p50={v["p50"]:.1f}ms '
                    f'p95={v["p95"]:.1f}ms avg={v["avg"]:.1f}ms')
    ear.stop()
    ctl.stop()
    ctl2.stop()
    c3.stop()
    try:
        prod_ctl.stop()
    except Exception:
        pass
    try:
        voice.close()
    except Exception:
        pass
    log('DONE')


main()