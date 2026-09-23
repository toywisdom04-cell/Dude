import collections
import os
import pickle
import queue
import re
import struct
import subprocess
import sys
import threading
import time

# Keep MKL from hard-aborting (and its per-thread buffers small) so a Whisper
# allocation hiccup degrades to a catchable error instead of killing the app.
# Must be set BEFORE numpy/MKL initializes.
os.environ.setdefault("KMP_ABORT_ON_MALLOC_FAILURE", "FALSE")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OMP_DYNAMIC", "FALSE")

import logging

import numpy as np
import sounddevice as sd

from core.config import get_config

log = logging.getLogger("dude")

SILERO_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"


def _rms_dbfs(frame_int16):
    x = frame_int16.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(x))) + 1e-9)
    return 20.0 * np.log10(rms)


# Whisper routinely "transcribes" meaningless fragments when given silence,
# room noise or the tail of the assistant's own voice. These are classic
# hallucination fillers. Only drop them when they are the ENTIRE short text,
# so genuine short commands/answers (e.g. "yes"/"no"/"hey dude") survive.
_HALLUCINATION_FILLERS = {
    "oh my god", "oh god", "oh my gosh", "oh my", "yes my god", "oh", "wow", "whoa",
    "bye", "bye bye", "see you", "see ya", "goodbye", "good bye", "ok bye",
    "you", "you're welcome", "you are welcome", "no problem", "no worries",
    "thank you for watching", "thanks for watching", "god", "hey", "hey hey",
    "ok", "okay", "ah", "uh", "um", "mm", "huh",
}


def _is_garbage_transcript(text):
    t = (text or "").strip()
    if not t:
        return True
    low = t.lower()
    if re.search(r"\b[a-z](?:\s*[-–—]\s*[a-z]){2,}\b", low):  # "f-f-f-m-s"
        return True
    if re.search(r"\b(?:[a-z] ){2,}[a-z]\b", low):  # "f f f m s"
        return True
    if re.search(r"(.)\1{3}", low):  # "hhhh", "eeee"
        return True
    words = low.split()
    chars = re.sub(r"[^a-z0-9]", "", low)
    if words and all(len(w) <= 1 for w in words) and len(chars) <= 2:  # "ff", "x"
        return True
    if len(words) <= 3 and low in _HALLUCINATION_FILLERS:
        return True
    alnum = re.findall(r"[a-z']+", low)
    if len(alnum) >= 2:  # "dude dude…", "hello dude dude dude" (Whisper latch loop)
        counts = {}
        for w in alnum:
            w2 = w.strip("'")
            if len(w2) >= 2:
                counts[w2] = counts.get(w2, 0) + 1
        if counts and max(counts.values()) >= max(3, len([w for w in alnum if len(w) >= 2]) - 1):
            return True
    if len(alnum) >= 4:  # repeated phrase loop: "how are you how are you how are you…"
        for size in (1, 2, 3):
            if len(alnum) % size:
                continue
            unit = alnum[:size]
            if all(alnum[i:i + size] == unit for i in range(size, len(alnum), size)):
                return True
    return False


class SileroVAD:
    def __init__(self, model_path):
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        self.input_names = {i.name.lower(): i.name for i in self.session.get_inputs()}
        self.output_names = {o.name.lower(): o.name for o in self.session.get_outputs()}
        self.is_v5 = any("state" in n for n in self.input_names)
        if self.is_v5:
            self.state = np.zeros((2, 1, 128), dtype=np.float32)
        else:
            self.h = np.zeros((2, 1, 64), dtype=np.float32)
            self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def prob(self, chunk_f32):
        feed = {}
        if self.is_v5:
            feed[self.input_names["input"]] = chunk_f32.reshape(1, -1).astype(np.float32)
            feed[self.input_names["state"]] = self.state
            feed[self.input_names["sr"]] = np.asarray(16000, dtype=np.int64)
            outs = self.session.run(None, feed)
            out_keys = list(self.output_names.keys())
            prob = None
            new_state = None
            for k, arr in zip(out_keys, outs):
                if "state" in k:
                    new_state = arr.astype(np.float32)
                elif arr.size == 1 or "output" in k:
                    prob = float(arr.reshape(-1)[0])
            if new_state is not None:
                self.state = new_state
            return prob if prob is not None else 0.0
        else:
            feed[self.input_names["input"]] = chunk_f32.astype(np.float32)
            feed[self.input_names["h"]] = self.h
            feed[self.input_names["c"]] = self.c
            feed[self.input_names["sr"]] = np.asarray(16000, dtype=np.int64)
            outs = self.session.run(None, feed)
            out_keys = list(self.output_names.keys())
            prob = 0.0
            for k, arr in zip(out_keys, outs):
                if k == "output":
                    prob = float(arr.reshape(-1)[0])
                elif k == "hn":
                    self.h = arr.astype(np.float32)
                elif k == "cn":
                    self.c = arr.astype(np.float32)
            return prob


class EnergyVAD:
    def __init__(self):
        self.floor = -45.0

    def update_floor(self, db):
        self.floor = max(-70.0, min(-20.0, 0.95 * self.floor + 0.05 * db))

    def prob(self, dbfs):
        margin = dbfs - self.floor
        if margin > 10:
            return 0.99
        if margin > 5:
            return 0.7
        return 0.05


class WebRTCVAD:
    """WebRTC voice activity detection (binary speech/no-speech).

    Exposes a ``prob(frame_f32)`` method returning 1.0 (speech) or 0.0
    (silence) so it is compatible with the VAD loop thresholds. A configurable
    RMS floor keeps quiet, far-field / room noise from ever counting as
    speech, so the assistant only reacts to reasonably loud nearby voice.
    """

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2,
                 min_rms: float = 0.012):
        import webrtcvad

        self._vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.min_rms = min_rms
        # WebRTC requires 10/20/30 ms frames. Use 30 ms sub-frames.
        self.sub_samples = int(sample_rate * 0.030)
        self.sub_bytes = self.sub_samples * 2  # 16-bit mono

    def prob(self, frame_f32):
        if self.min_rms:
            rms = float(np.sqrt(np.mean(np.square(frame_f32))) + 1e-9)
            if rms < self.min_rms:
                return 0.0
        frame_int16 = np.clip(frame_f32, -1.0, 1.0) * 32768
        pcm = frame_int16.astype(np.int16).tobytes()
        if len(pcm) >= self.sub_bytes:
            sub = pcm[:self.sub_bytes]
            try:
                return 1.0 if self._vad.is_speech(sub, self.sample_rate) else 0.0
            except Exception:
                return 0.0
        return 0.0


class Ear:
    def __init__(self, voice_speaking_flag=None, on_barge_in=None, on_level=None,
                 on_speech_scheduled=None):
        self.cfg = get_config()
        self.on_level = on_level or (lambda lvl: None)
        self.capturing_event = threading.Event()
        self.sample_rate = int(self.cfg.get("ear", "sample_rate", default=16000))
        self.frame_ms = int(self.cfg.get("ear", "frame_ms", default=32))
        self.frame_len = self.sample_rate * self.frame_ms // 1000
        self.silence_end_frames = int(
            int(self.cfg.get("ear", "silence_end_ms", default=750)) / self.frame_ms)
        self.min_speech_frames = int(
            int(self.cfg.get("ear", "min_speech_ms", default=250)) / self.frame_ms)
        self.interrupt_min_frames = int(
            int(self.cfg.get("ear", "interrupt_min_speech_ms", default=350)) / self.frame_ms)

        self.mic_gain = float(self.cfg.get("ear", "mic_gain", default=15.0))

        self.voice_speaking = voice_speaking_flag or (lambda: False)
        self.on_barge_in = on_barge_in or (lambda: None)
        self._speech_scheduled = on_speech_scheduled or (lambda text: None)

        self.text_q = queue.Queue()
        self._utt_q = queue.Queue(maxsize=64)
        self._audio_q = queue.Queue(maxsize=400)
        self._frames = collections.deque(maxlen=200)
        self._running = True
        self._last_frame_ts = time.time()
        self._mic_peak = 0.0
        self._echo_until = 0.0
        self._lockout_until = 0.0
        self._last_choice = None

        # Barge-in: while we are talking, watch for the user cutting back in.
        # Note: on speaker+mic setups our own TTS echoes back at the mic with
        # near-full-scale peaks, so barge-in can cut DUDE off on ITSELF. It is
        # therefore OFF by default; enable with {"ear": {"barge_in": 1}} in
        # config.json when using a headset or when DUDE's audio doesn't reach
        # the mic.
        barge_val = self.cfg.get("ear", "barge_in", default=0)
        self.barge_enabled = bool(barge_val)
        self.barge_sustain = int(self.cfg.get("ear", "barge_sustain_frames", default=8))
        self._barge_ready_at = float("inf")
        self._barge_frames = 0
        self._in_barge_capture = False

        self.vad = None
        mode = self.cfg.get("ear", "vad", default="auto")
        model_path = os.path.join(self.cfg.data_dir, "models", "silero_vad.onnx")

        # Prefer WebRTC VAD (reliable, matches working C-drive project)
        if mode in ("auto", "webrtc"):
            try:
                min_rms = float(self.cfg.get("ear", "vad_min_rms", default=0.012))
                aggr = int(self.cfg.get("ear", "vad_aggressiveness", default=1))
                self.vad = WebRTCVAD(sample_rate=self.sample_rate, min_rms=min_rms,
                                     aggressiveness=aggr)
                print(f"[ear] WebRTC VAD active (min_rms={min_rms:.3f}, aggr={aggr})")
                log.info("ear: WebRTC VAD active (min_rms=%s)", min_rms)
            except Exception as e:
                print(f"[ear] webrtcvad unavailable ({e}); trying silero")
                self.vad = None

        if self.vad is None and mode in ("auto", "silero"):
            if not os.path.exists(model_path):
                try:
                    import urllib.request

                    req = urllib.request.Request(SILERO_URL, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=30) as r, open(model_path, "wb") as f:
                        f.write(r.read())
                except Exception as e:
                    print(f"[ear] silero download failed ({e}); using energy VAD")
            if os.path.exists(model_path):
                try:
                    self.vad = SileroVAD(model_path)
                    print("[ear] Silero VAD active")
                except Exception as e:
                    print(f"[ear] silero load failed ({e}); using energy VAD")
                    self.vad = None

        if self.vad is None and mode in ("auto", "energy"):
            self.vad = EnergyVAD()
            print("[ear] Energy VAD active")

        self.whisper = None
        self._stt_proc = None
        self._stt_resp_q = None
        self._stt_reader = None

        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True, name="du-mic")
        self._vad_thread = threading.Thread(target=self._vad_loop, daemon=True, name="du-vad")
        self._stt_thread = threading.Thread(target=self._stt_loop, daemon=True, name="du-stt")

    def note_speech(self, text):
        """Time-based echo suppression: whenever the assistant is about to speak,
        mute the microphone for the estimated speech duration (+ tail). This does
        not rely on pygame's get_busy(), which can clear early on flaky drivers."""
        words = max(1, len((text or "").split()))
        est = max(1.5, words / 2.7)
        if self.barge_enabled:
            warmup = float(self.cfg.get("ear", "barge_warmup_ms", default=1000)) / 1000.0
            self._barge_ready_at = time.time() + warmup
        self._echo_until = max(self._echo_until, time.time() + est + 1.2)

    def _resolve_input_device(self):
        """Return an input device that actually delivers stream callbacks.
        Intel Smart Sound exposes many aliases of the same built-in mic (default,
        Array 1..4, WMME/DS copies), but only the currently-live endpoint fires
        callbacks. A static index, or the OS default, is therefore not reliable;
        we briefly open a stream on candidates and keep the first live one."""
        requested = self.cfg.get("ear", "mic_index", default=None)

        def _is_mic(i):
            try:
                name = (sd.query_devices(i).get("name") or "").lower()
            except Exception:
                return True
            return not ("mix" in name or "loopback" in name or "what u hear" in name)

        def _delivers(i):
            if i is None or not _is_mic(i):
                return False
            try:
                sd.check_input_settings(device=i, channels=1,
                                        samplerate=self.sample_rate, dtype="int16")
            except Exception:
                return False
            count = [0]

            def cb(indata, frames, t, st):
                count[0] += 1

            try:
                seconds = 0.6
                with sd.RawInputStream(samplerate=self.sample_rate, blocksize=self.frame_len,
                                       dtype="int16", channels=1, callback=cb, device=i):
                    time.sleep(seconds)
                min_cb = max(2, int(seconds * self.sample_rate / max(self.frame_len, 1) * 0.25))
                return count[0] >= min_cb
            except Exception:
                return False

        prev = getattr(self, "_last_choice", None)
        candidates = []
        if prev is not None and prev >= 0:
            candidates.append(("known-good", prev))
        if requested is not None and requested >= 0:
            candidates.append(("configured", requested))
        try:
            d = sd.default.device[0]
        except Exception:
            d = None
        if d is not None and d >= 0:
            candidates.append(("default", d))
        for label, candidate in candidates:
            if candidate in (None, -1):
                continue
            if _delivers(candidate):
                self._last_choice = candidate
                if label == "configured":
                    log.info("ear: using configured device %s", candidate)
                elif label == "known-good":
                    log.info("ear: reusing known-good device %s", candidate)
                else:
                    log.info("ear: using default device %s", candidate)
                print(f"[ear] mic: using {label} device {candidate} (delivers audio)")
                return candidate
        for i, dev in enumerate(sd.query_devices()):
            if i in (c[1] for c in candidates) or dev.get("max_input_channels", 0) <= 0:
                continue
            if _delivers(i):
                self._last_choice = i
                print(f"[ear] mic: live device {i} ({dev.get('name')}) delivers audio")
                log.info("ear: using live device %s (%s)", i, dev.get("name"))
                return i
        if requested is not None and requested >= 0:
            self._last_choice = requested
            return requested
        if d is not None and d >= 0:
            self._last_choice = d
            return d
        raise RuntimeError("no usable microphone input device")

    def start(self):
        try:
            import numpy as _np

            dur = 1.5
            dev = self._resolve_input_device()
            rec = sd.rec(int(dur * self.sample_rate), samplerate=self.sample_rate,
                         channels=1, dtype="int16", device=dev)
            sd.wait()
            peak = float(_np.max(_np.abs(rec))) / 32768.0
            if peak < 0.004:
                print(f"[mic-check] WARNING: microphone near-silent (peak={peak:.4f}). "
                      f"Check Windows Settings > Privacy & security > Microphone -> "
                      f"allow desktop apps, and your input device/gain.")
                log.warning("ear: mic-check near-silent (peak=%.4f)", peak)
            else:
                print(f"[mic-check] microphone hears you (peak={peak:.3f})")
                log.info("ear: mic-check OK (peak=%.3f device=%s)", peak, dev or "default")
        except Exception as e:
            print(f"[mic-check] failed: {e}")
            log.exception("ear: mic-check failed")
        self._mic_thread.start()
        self._vad_thread.start()
        self._stt_thread.start()

    def stop(self):
        self._running = False
        if self._stt_proc is not None and self._stt_proc.poll() is None:
            try:
                self._stt_write(("bye",))
            except Exception:
                pass
            try:
                self._stt_proc.terminate()
            except Exception:
                pass

    def pop_utterance(self, block=False, timeout=None):
        try:
            return self.text_q.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def _stt_write(self, obj):
        data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        self._stt_proc.stdin.write(struct.pack(">I", len(data)) + data)
        self._stt_proc.stdin.flush()

    def _stt_reader_run(self):
        stream = self._stt_proc.stdout
        while True:
            try:
                hdr = stream.read(4)
            except Exception:  # noqa: BLE001
                break
            if not hdr or len(hdr) < 4:
                break
            (n,) = struct.unpack(">I", hdr)
            data = stream.read(n)
            if len(data) < n:
                break
            try:
                self._stt_resp_q.put(pickle.loads(data))
            except Exception:  # noqa: BLE001
                break
        # Signal worker death
        self._stt_resp_q.put(("worker_died", None))

    def _start_stt_reader(self):
        self._stt_resp_q = queue.Queue()
        self._stt_reader = threading.Thread(
            target=self._stt_reader_run, daemon=True, name="du-stt-reader")
        self._stt_reader.start()

    def warm_start(self):
        """Preload the STT (whisper) worker at boot so speech-to-text is ready
        the moment the user talks — lazy loading under memory pressure is what
        made the worker miss audio. Any failure here shows up in the logs
        immediately instead of silently after the first utterance."""
        try:
            return self._ensure_whisper()
        except Exception as e:
            print(f"[ear] whisper warm-start failed: {e}")
            return False

    def _ensure_whisper(self):
        # Clean up dead process
        if self._stt_proc is not None:
            if self._stt_proc.poll() is None:
                return True
            # Process died, clean up
            try:
                self._stt_proc.terminate()
            except Exception:
                pass
            self._stt_proc = None
        
        try:
            model_name = self.cfg.get("ear", "whisper_model", default="base.en")
            print(f"[ear] loading whisper '{model_name}' (subprocess) ...")
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--stt-worker", model_name]
            else:
                worker_py = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "stt_worker.py")
                cmd = [sys.executable, "-u", worker_py, model_name]
            worker_err = open(os.path.join(self.cfg.data_dir, "logs", "stt_worker.log"), "a", encoding="utf-8")
            # Use CREATE_NO_WINDOW on Windows to prevent console window issues
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            self._stt_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=worker_err,
                bufsize=0,
                creationflags=creationflags,
            )
            self._start_stt_reader()
            t0 = time.time()
            while time.time() - t0 < 90:
                try:
                    kind, val = self._stt_resp_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if kind == "ready":
                    print(f"[ear] whisper ready (subprocess) in {time.time()-t0:.1f}s")
                    return True
                if kind == "load_failed":
                    print(f"[ear] whisper load failed: {val}")
                    return False
                if kind == "worker_died":
                    print("[ear] worker died during startup")
                    return False
            print("[ear] whisper subprocess load timed out")
            return False
        except Exception as e:
            print(f"[ear] whisper subprocess error: {e}")
            return False

    def _transcribe(self, audio, lang):
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                self._stt_write((audio, lang))
                kind, val = self._stt_resp_q.get(timeout=180)
            except queue.Empty:
                print("[ear] stt timed out")
                if attempt < max_retries:
                    print(f"[ear] restarting whisper worker (attempt {attempt + 1})")
                    self._ensure_whisper()
                    continue
                return ""
            except Exception as e:
                print(f"[ear] stt comms error: {e}")
                if attempt < max_retries:
                    self._ensure_whisper()
                    continue
                return ""
            if kind == "text":
                return val
            if kind == "error":
                print(f"[ear] stt worker error: {val}")
                if attempt < max_retries:
                    self._ensure_whisper()
                    continue
                return ""
            if kind == "worker_died":
                print("[ear] stt worker died, restarting...")
                if attempt < max_retries:
                    self._ensure_whisper()
                    continue
                return ""
        return ""

    def _mic_callback(self, indata, frames, time_info, status):
        try:
            self._audio_q.put_nowait(bytes(indata))
        except queue.Full:
            pass
        self._last_frame_ts = time.time()
        x = np.frombuffer(indata, dtype=np.int16)
        if x.size:
            p = float(np.abs(x).max()) / 32768.0
            if p > self._mic_peak:
                self._mic_peak = p

    def _mic_loop(self):
        failures = 0
        while self._running:
            try:
                mic_index = self._resolve_input_device()
            except Exception as e:
                log.error("ear: no usable mic: %s", e)
                time.sleep(2)
                continue
            log.info("ear: opening RawInputStream (device=%s)", mic_index)
            try:
                with sd.RawInputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.frame_len,
                    dtype="int16",
                    channels=1,
                    callback=self._mic_callback,
                    device=mic_index,
                ):
                    self._last_frame_ts = time.time()
                    self._mic_peak = 0.0
                    print(f"[ear] microphone LIVE on device {mic_index}")
                    log.info("ear: RawInputStream started successfully (device=%s)", mic_index)
                    while self._running:
                        time.sleep(0.5)
                        if time.time() - self._last_frame_ts > 4.0:
                            failures += 1
                            log.warning("ear: no mic frames for 4s (device %s went away); re-opening "
                                        "(fail=%d)", mic_index, failures)
                            if failures >= 2:
                                self._last_choice = None
                                failures = 0
                                log.warning("ear: device %s keeps stalling; forcing a fresh device search", mic_index)
                            break
                        failures = 0
            except Exception as e:
                print(f"[ear] microphone error: {e}")
                log.warning("ear: mic stream error (%s); retrying in 1s", e)
                time.sleep(1)

    def _vad_loop(self):
        speech_buf = []
        in_speech = False
        speech_start_ts = 0.0
        silence_run = 0
        speech_run = 0
        barge_run = 0
        barge_silence = 0
        barge_fired = False
        voiced_run = 0
        max_voiced_run = 0
        # Allow a few adjacent non-speech frames while an interruption is being
        # detected without cancelling it. WebRTC VAD is binary and jumpy: a
        # brief inter-word / breath dip would otherwise reset the counter every
        # time and make barge-in almost never fire.
        BARGE_TOLERANCE = 4
        PRE_ROLL = 4
        pre_roll = []
        while self._running:
            try:
                raw = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            frame = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

            # Apply microphone gain boost
            frame = frame * self.mic_gain
            frame = np.clip(frame, -1.0, 1.0)

            try:
                lvl = min(1.0, float(np.sqrt(np.mean(np.square(frame)))) * 8.0)
                self.on_level(lvl)
            except Exception:
                pass

            if isinstance(self.vad, SileroVAD) or isinstance(self.vad, WebRTCVAD):
                prob = self.vad.prob(frame)
            else:
                db = _rms_dbfs(np.frombuffer(raw, dtype=np.int16))
                self.vad.update_floor(db)
                prob = self.vad.prob(db)

            # While the assistant is audible frames are normally muted so our own
            # TTS through the speakers can't be transcribed as the user's words.
            # With barge_in enabled we instead also watch for the user's voice
            # cutting back in mid-speech: on a sustained voiced run we stop our
            # speech and immediately start capturing what he is saying. Once
            # voice.interrupt() halts playback, voice_speaking() flips false and
            # his remaining words fall through to the normal capture path.
            speaking = self.voice_speaking()
            now = time.time()
            if speaking:
                self._echo_until = now + 0.6

            if speaking and self.barge_enabled and not self._in_barge_capture \
                    and now >= self._barge_ready_at:
                if prob >= 0.5:
                    self._barge_frames += 1
                    if self._barge_frames >= self.barge_sustain:
                        self._barge_frames = 0
                        self._in_barge_capture = True
                        self._echo_until = max(self._echo_until, now + 0.150)
                        self._lockout_until = max(self._lockout_until, now + 0.150)
                        try:
                            self.on_barge_in()
                        except Exception:
                            pass
                else:
                    self._barge_frames = 0

            if speaking or now < self._echo_until or now < self._lockout_until:
                prob = 0.0
            if speaking:
                if self._in_barge_capture and not self.voice_speaking():
                    self._in_barge_capture = False
                    if not in_speech:
                        in_speech = True
                        speech_start_ts = time.time() - 0.2
                        speech_buf = list(pre_roll) + [frame]
                        silence_run = 0
                        voiced_run = 0
                        max_voiced_run = 0
                        self.capturing_event.set()
                continue

            if not in_speech:
                barge_run = 0
                barge_silence = 0
                if prob > 0.15:
                    speech_run += 1
                    if speech_run >= 2:
                        in_speech = True
                        speech_start_ts = time.time()
                        speech_buf = list(pre_roll) + [frame]
                        silence_run = 0
                        self.capturing_event.set()
                        self._utt_meta = {"cap_start": speech_start_ts,
                                          "vad_start": speech_start_ts}
                else:
                    speech_run = 0
                pre_roll.append(frame)
                pre_roll = pre_roll[-PRE_ROLL:]
                continue

            speech_buf.append(frame)
            if prob >= 0.5:
                voiced_run += 1
                if voiced_run > max_voiced_run:
                    max_voiced_run = voiced_run
            else:
                voiced_run = 0
            if prob < 0.15:
                silence_run += 1
            else:
                silence_run = 0
            if (silence_run >= self.silence_end_frames) or \
                    (speech_start_ts and time.time() - speech_start_ts > 12.0):
                total_frames = len(speech_buf)
                # Sustained voicing + duration requirement: Whisper hallucinates
                # whole phrases out of short noise blips and near-silent clipping.
                # Requiring >=500ms of real audio and a sustained voiced run blocks
                # those without hurting real words ("dude" keeps ~0.2s+ of voicing).
                if total_frames >= 16 and max_voiced_run >= 8:
                    audio = np.concatenate(speech_buf)
                    meta = dict(getattr(self, "_utt_meta", {}) or {})
                    meta["speech_end"] = time.time()
                    self._utt_q.put(("utterance", audio, meta))
                speech_buf = []
                in_speech = False
                barge_fired = False
                voiced_run = 0
                max_voiced_run = 0
                pre_roll = []
                speech_start_ts = 0.0
                self._lockout_until = time.time() + 0.6
                self.capturing_event.clear()

    def _stt_loop(self):
        while self._running:
            try:
                item = self._utt_q.get(timeout=0.5)
            except queue.Empty:
                continue
            kind, audio = item[0], item[1]
            meta = item[2] if len(item) > 2 else {}
            if kind != "utterance":
                continue
            print(f"[ear] utterance captured: {len(audio)} samples, peak={float(np.max(np.abs(audio))):.3f}")
            log.info("ear: utterance captured: %d samples, peak=%.3f", len(audio),
                     float(np.max(np.abs(audio))))
            log.info("STT_SPEECH_END samples=%d peak=%.3f",
                     len(audio), float(np.max(np.abs(audio))))
            if len(audio) < int(0.3 * self.sample_rate):
                print(f"[ear] skipped: utterance too short ({len(audio)} samples)")
                log.info("ear: skipped short utterance (%d samples)", len(audio))
                continue
            rms = float(np.sqrt(np.mean(np.square(audio))))
            min_utt_rms = self.cfg.get("ear", "min_utt_rms", default=0.008)
            if rms < min_utt_rms:
                print(f"[ear] skipped: low-energy noise (rms={rms:.4f}, threshold={min_utt_rms:.4f})")
                log.info("ear: skipped noise (rms=%.4f, threshold=%.4f)", rms, min_utt_rms)
                continue
            log.info("ear: utterance accepted for STT (samples=%d, rms=%.4f)", len(audio), rms)
            if not self._ensure_whisper():
                continue
            lang = self.cfg.get("ear", "language", default="en")
            log.info("STT_TRANSCRIBE_START model=%s lang=%s samples=%d",
                     self.cfg.get("ear", "whisper_model", default="base.en"),
                     lang, len(audio))
            _t_stt = time.time()
            text = self._transcribe(audio, lang)
            print(f"[ear] transcription: {text!r}")
            log.info('STT_RESULT %r', text)
            try:
                _t = time.time()
                log.info("UTT_TRACE cap_ms=%.0f speech_ms=%.0f stt_ms=%.0f "
                         "samples=%d chars=%d",
                         (meta.get("speech_end", _t) -
                          meta.get("cap_start", _t)) * 1000.0,
                         (meta.get("speech_end", _t) -
                          meta.get("vad_start", meta.get("speech_end", _t))) * 1000.0,
                         (_t - _t_stt) * 1000.0,
                         len(audio), len(text or ""))
            except Exception:
                pass
            if text and _is_garbage_transcript(text):
                print(f"[ear] discarded hallucinated phrase: {text!r}")
                log.info("ear: discarded hallucinated phrase: %r", text)
                continue
            if text:
                log.info("TRANSCRIPT_ACCEPTED text=%r chars=%d", text, len(text))
                self.text_q.put(("text", text))