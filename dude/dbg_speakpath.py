"""Trace which Voice speak path holds `speaking` after a barge-in stop."""
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

# ---- instrument Voice paths ----
_orig_play = Voice._play
_orig_fallback = Voice._fallback_speak
_orig_synth = Voice._synth


def traced_play(self, path):
    log(f'PATH _play enter stop_set={self._stop_evt.is_set()}')
    try:
        return _orig_play(self, path)
    finally:
        log(f'PATH _play exit speaking={self._speaking.is_set()}')


def traced_fallback(self, sentence):
    log(f'PATH _fallback_speak enter len={len(sentence)}')
    try:
        return _orig_fallback(self, sentence)
    finally:
        log('PATH _fallback_speak exit')


def traced_synth(self, sentence):
    log(f'PATH _synth enter len={len(sentence)}')
    try:
        out = _orig_synth(self, sentence)
        log('PATH _synth ok')
        return out
    except Exception as e:
        log(f'PATH _synth FAILED: {type(e).__name__}: {e}')
        raise


Voice._play = traced_play
Voice._fallback_speak = traced_fallback
Voice._synth = traced_synth

import tempfile

events = {}
voice = Voice()
ear = Ear(voice_speaking_flag=lambda: voice.speaking, on_barge_in=lambda: None)
voice.on_speech_scheduled = ear.note_speech
ear.start()
time.sleep(2)
assert ear.warm_start(), 'whisper failed'

adapter = VoiceEngineAdapter(voice)
ctlD = RealtimeInteractionController(
    tts=adapter, stt=FakeSTTBackend(''), memory=None)


class StubMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


ctlD.memory = StubMemory()


def on_barge2():
    stamp('INTERRUPT_REQUEST')
    events['barged'] = True
    voice.interrupt()
    ctlD.signal_user_speech()


ear.on_barge_in = on_barge2
ear.barge_enabled = True
voice.on_speech_scheduled = lambda text: None

long_text = ("This is a deliberately long spoken response so there is "
             "plenty of time to interrupt me while I am still talking. "
             "Keep listening for the interruption cue which arrives "
             "a few seconds from now. There is even more speech here so "
             "the talker would ramble on and on if nobody stopped it.")
stamp('TTS_START')
ctlD._force(VoiceState.SPEAKING)
spk = threading.Thread(target=lambda: adapter.speak(long_text), daemon=True)
spk.start()
t0 = time.time()
while not adapter.is_speaking() and time.time() - t0 < 30:
    time.sleep(0.05)
time.sleep(2.5)
while ear.pop_utterance(block=False) is not None:
    pass
tmp = tempfile.mkdtemp()
intr_wav = os.path.join(tmp, 'stop.wav')
import asyncio
import edge_tts


async def render():
    await edge_tts.Communicate('Stop!', voice='en-US-ChristopherNeural').save(intr_wav)


asyncio.run(render())
import sounddevice as sd
import soundfile as sf
arr, sr = sf.read(intr_wav, dtype='float32', always_2d=True)
ear._barge_ready_at = time.time()
stamp('INTERRUPT_PLAYBACK_START')
sd.play(arr.mean(axis=1).astype('float32'), sr)
sd.wait()
t_end = time.time() + 6
while time.time() < t_end:
    if ctlD.state is VoiceState.INTERRUPTED and not adapter.is_speaking():
        break
    time.sleep(0.05)
stamp('TTS_STOPPED')
log(f'state={ctlD.state.value} speaking={adapter.is_speaking()} '
    f'stop_set={voice._stop_evt.is_set()} queue_empty={voice.q.empty()}')
sightings = []
t1 = time.time()
while time.time() - t1 < 4.0:
    if adapter.is_speaking():
        sightings.append(round(time.time() - t1, 2))
    time.sleep(0.05)
log(f'sightings={sightings} stop_set={voice._stop_evt.is_set()} '
    f'queue_empty={voice.q.empty()} worker_alive={voice._thread.is_alive()}')
ear.stop()
ctlD.stop()
try:
    voice.close()
except Exception:
    pass
log('DONE')