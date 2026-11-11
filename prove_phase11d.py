"""Focused barge-in rerun: stop-sticks proof + stop-command continuation."""
import os
import sys
import threading
import time

sys.path.insert(0, r'E:\Dude\dude')


def log(msg):
    print(msg, flush=True)


TS = {}


def stamp(name):
    TS[name] = time.time()
    print(f"[{name}] t={TS[name]:.2f}", flush=True)


from core.ear import Ear
from core.voice import Voice
from core.orchestrator.realtime_voice import (
    RealtimeInteractionController, FakeSTTBackend, VoiceEngineAdapter,
    VoiceState,
)
class StubMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


def edge_render(text, path, timeout=30):
    import asyncio
    import edge_tts

    async def run():
        com = edge_tts.Communicate(text, voice='en-US-ChristopherNeural')
        await asyncio.wait_for(com.save(path), timeout=timeout)

    asyncio.run(run())
    return os.path.getsize(path) > 1000


def play_wav(path, gain=0.5):
    import sounddevice as sd
    import wave
    import numpy as np
    try:
        with wave.open(path, 'rb') as w:
            raw = w.readframes(w.getnframes())
            sr, ch = w.getframerate(), w.getnchannels()
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            arr = arr.reshape(-1, ch).mean(axis=1)
    except wave.Error:
        import soundfile as sf
        arr, sr = sf.read(path, dtype='float32', always_2d=True)
        arr = arr.mean(axis=1).astype(np.float32)
    sd.play(arr * gain, sr)
    sd.wait()

events = {}
voice = Voice()
ear = Ear(voice_speaking_flag=lambda: voice.speaking, on_barge_in=lambda: None)
voice.on_speech_scheduled = ear.note_speech
log('ear starting')
ear.start()
time.sleep(2)
assert ear.warm_start(), 'whisper failed'

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
voice.on_speech_scheduled = lambda text: None  # detach echo re-arm (test only)

import tempfile
tmp = tempfile.mkdtemp()
long_text = ("This is a deliberately long spoken response so there is "
             "plenty of time to interrupt me while I am still talking. "
             "Keep listening for the interruption cue which arrives "
             "a few seconds from now. There is even more speech here so "
             "the talker would ramble on and on if nobody stopped it.")
log('speaking long text via production Voice engine')
stamp('TTS_START')
ctlD._force(VoiceState.SPEAKING)
epoch = {"n": 0}


def _should_stop():
    return epoch["n"] != 0


_orig_barge = on_barge2


def _on_barge_tracked():
    _orig_barge()
    epoch["n"] += 1


ear.on_barge_in = _on_barge_tracked
spk = threading.Thread(
    target=lambda: adapter.speak(long_text, should_stop=_should_stop),
    daemon=True)
spk.start()
t0 = time.time()
while not adapter.is_speaking() and time.time() - t0 < 30:
    time.sleep(0.05)
assert adapter.is_speaking(), 'TTS never started'
time.sleep(2.5)
while ear.pop_utterance(block=False) is not None:
    pass
intr_wav = os.path.join(tmp, 'stop.wav')
assert edge_render('Stop!', intr_wav)
ear._barge_ready_at = time.time()
stamp('INTERRUPT_PLAYBACK_START')
play_wav(intr_wav, gain=1.0)
t_end = time.time() + 8
while time.time() < t_end:
    if ctlD.state is VoiceState.INTERRUPTED and not adapter.is_speaking():
        break
    time.sleep(0.05)
stamp('TTS_STOPPED')
assert ctlD.state is VoiceState.INTERRUPTED, 'no interruption event'
assert not adapter.is_speaking(), 'playback did not stop'
fire, play0 = TS.get('INTERRUPT_REQUEST', 0), TS.get('INTERRUPT_PLAYBACK_START', 0)
assert fire and fire > play0, 'barge not attributable to interruption'
log(f'attributed barge +{fire - play0:.2f}s after playback')

# stop-sticks: no resumption of queued/remainder speech for 4s.
# Timestamped: speaking True only within the first 0.5s is legitimate
# audio-buffer drain; anything later is a genuine continuation bug.
sightings = []
t1 = time.time()
while time.time() - t1 < 4.0:
    if adapter.is_speaking():
        sightings.append(round(time.time() - t1, 2))
    time.sleep(0.05)
quiet = not any(s > 0.5 for s in sightings)
log(f'speaking_sightings={sightings} queue_empty={voice.q.empty()}')
log(f'stop_sticks_4s={quiet} queue_empty={voice.q.empty()}')
assert quiet and voice.q.empty(), 'speech resumed after stop'

item = ear.pop_utterance(block=True, timeout=60)
assert item, 'no interruption utterance'
kind, stt_text = item
stamp('STT_RESULT')
log(f'STT_RESULT={stt_text!r}')
reply = ctlD.on_final_text(stt_text)
stamp('NEXT_RESPONSE_START')
log(f'reply={reply!r} state={ctlD.state.value}')
ctlD.wait_spoken(timeout=30)
log(f'final state={ctlD.state.value}')
assert ctlD.state is VoiceState.LISTENING

# stop-command continuation path (integration level, deterministic)
r2 = ctlD.on_final_text('stop')
ctlD.wait_spoken(timeout=20)
log(f'stop-command reply={r2!r} state={ctlD.state.value}')
assert r2 == 'Stopped.', f'unexpected stop reply: {r2!r}'

ear.stop()
ctlD.stop()
try:
    voice.close()
except Exception:
    pass
log('DONE')
