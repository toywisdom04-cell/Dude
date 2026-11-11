import asyncio
import logging
import os
import queue
import tempfile
import threading
import time

from core.config import get_config

log = logging.getLogger("dude")


class Voice:
    def __init__(self, on_speech_scheduled=None):
        self.cfg = get_config()
        self.q = queue.Queue()
        self._stop_evt = threading.Event()
        self._speaking = threading.Event()
        self.interrupted = False
        self.silent = False
        self.on_speech_scheduled = on_speech_scheduled
        self._thread = threading.Thread(target=self._worker, daemon=True, name="du-voice")
        self._thread.start()
        self._mixer_ready = False

    # ---------- public ----------
    def say(self, text, priority=False, force=False):
        text = (text or "").strip()
        if not text:
            return
        if self.silent and not force:
            log.info("voice: suppressed by quiet mode: %r", text[:80])
            return
        log.info("voice: say: %r", text[:80])
        if self.on_speech_scheduled:
            try:
                self.on_speech_scheduled(text)
            except Exception:
                pass
        if priority:
            self.interrupt()
            self.q.put(text)
            return
        self.q.put(text)

    def interrupt(self):
        self.interrupted = True
        self._stop_evt.set()

    def abort(self):
        self.interrupt()
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    @property
    def speaking(self):
        return self._speaking.is_set()

    def wait_done(self, timeout=30):
        t0 = time.time()
        while (not self.q.empty() or self.speaking) and time.time() - t0 < timeout:
            time.sleep(0.05)

    def close(self):
        self.interrupt()
        self.q.put(None)

    # ---------- internals ----------
    def _ensure_mixer(self, force_reset=False):
        if self._mixer_ready and not force_reset:
            return True
        try:
            import pygame
            import sounddevice as sd
            if force_reset and self._mixer_ready:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
                self._mixer_ready = False
            dev = self.cfg.get("voice", "output_device", default=None)
            sr = 44100
            try:
                if dev is not None:
                    info = sd.query_devices(int(dev), "output")
                    sr = int(info["default_samplerate"])
            except Exception:
                pass
            pygame.mixer.pre_init(sr, -16, 2, 4096)
            pygame.mixer.init()
            self._mixer_ready = True
            return True
        except Exception as e:
            print(f"[voice] mixer init failed: {e}")
            log.warning("voice: mixer init failed: %s", e)
            self._mixer_ready = False
            return False

    def _worker(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            self.interrupted = False
            self._stop_evt.clear()
            self._speak_text(item)
            self._speaking.clear()

    def _speak_text(self, text):
        log.info("voice: speaking item: %r", text[:100])
        cap = max(1, int(self.cfg.get("voice", "max_spoken_sentences", default=2)))
        sentences = self._sentences(self._sanitize_for_tts(text))[:cap]
        if not sentences:
            return
        if self._stop_evt.is_set():
            return
        combined = " ".join(sentences)
        path = None
        try:
            path = self._synth(combined)
        except Exception as e:
            print(f"[voice] tts failed, using SAPI fallback: {e}")
            log.warning("voice: tts failed, SAPI fallback: %s", e)
            self._fallback_speak(combined)
            return
        if self.on_speech_scheduled:
            try:
                self.on_speech_scheduled(combined)
            except Exception:
                pass
        try:
            played = self._play(path)
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        if not played and not self._stop_evt.is_set():
            log.warning("voice: mixer playback unavailable, using SAPI fallback")
            self._fallback_speak(combined)

    @staticmethod
    def _sanitize_for_tts(text):
        """Make text spoken-friendly so the TTS doesn't spell out paths/underscores."""
        import re
        t = text or ""
        t = re.sub(r'[A-Za-z]:\\(?:[^ \t\r\n]+\\)*[^ \t\r\n]*', 'a file on your system', t)
        t = re.sub(r'\\\\[^ \t\r\n]+', 'a network location', t)
        t = t.replace("\\", " ").replace("_", " ")
        t = re.sub(r"\s{2,}", " ", t)
        return t.strip()

    @staticmethod
    def _sentences(text):
        parts, cur = [], ""
        for ch in text:
            cur += ch
            if ch in ".!?\n" and len(cur.strip()) > 2:
                parts.append(cur.strip())
                cur = ""
        if cur.strip():
            parts.append(cur.strip())
        merged = []
        for p in parts:
            if merged and len(merged[-1]) < 25:
                merged[-1] = merged[-1] + " " + p
            else:
                merged.append(p)
        return [p for p in merged if p]

    def _synth(self, sentence):
        import edge_tts

        voice = self.cfg.get("voice", "tts_voice", default="en-US-ChristopherNeural")
        rate = self.cfg.get("voice", "rate", default="+0%")
        volume = self.cfg.get("voice", "volume", default="+0%")

        async def run():
            com = edge_tts.Communicate(sentence, voice=voice, rate=rate, volume=volume)
            fd, path = tempfile.mkstemp(suffix=".mp3", dir=self.cfg.data_dir + "/tts_cache")
            os.close(fd)
            await asyncio.wait_for(com.save(path), timeout=25)
            return path

        return asyncio.run(run())

    def _play(self, path):
        try:
            import sounddevice as sd
            import numpy as np
            import pygame

            if not self._ensure_mixer():
                log.warning("voice: no mixer for decode, playback failed")
                return False

            output_device = self.cfg.get("voice", "output_device", default=None)
            if output_device is not None:
                output_device = int(output_device)

            sound = pygame.mixer.Sound(path)
            raw = sound.get_raw()
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            sr = pygame.mixer.get_init()[0]
            channels = pygame.mixer.get_init()[2] or 2
            if channels == 1:
                arr = np.repeat(arr, 2)
            arr = arr[: (len(arr) // 2) * 2]

            self._speaking.set()
            log.info("voice: playing reply audio (device=%s sr=%d)", output_device, sr)

            done_event = threading.Event()

            def callback(outdata, frames, time_info, status):
                if self._stop_evt.is_set() or done_event.is_set():
                    outdata[:] = 0
                    raise sd.CallbackStop()
                n = min(frames, (len(arr) - callback.idx) // 2)
                if n > 0:
                    two = arr[callback.idx:callback.idx + n * 2].reshape(-1, 2)
                    outdata[:n] = two
                    outdata[n:] = 0
                    callback.idx += n * 2
                else:
                    outdata[:] = 0
                    done_event.set()
                    raise sd.CallbackStop()

            callback.idx = 0

            def open_stream():
                try:
                    dev_info = sd.query_devices(output_device)
                    max_ch = int(dev_info.get("max_output_channels") or 2)
                except Exception:
                    max_ch = 2
                want_ch = min(2, max_ch)
                if want_ch < 1:
                    want_ch = 2
                return sd.OutputStream(
                    samplerate=sr, channels=want_ch, dtype="float32",
                    callback=callback, device=output_device,
                    blocksize=1024, latency=0.15,
                )

            stream = None
            last_err = None
            for attempt in range(3):
                try:
                    stream = open_stream()
                    break
                except Exception as e:
                    last_err = e
                    log.warning("voice: open device %s attempt %d failed: %s", output_device, attempt + 1, e)
                    time.sleep(0.4)
            if stream is None:
                raise last_err

            with stream:
                while not self._stop_evt.is_set() and not done_event.is_set():
                    time.sleep(0.02)

            self._speaking.clear()
            return True
        except Exception as e:
            print(f"[voice] playback failed: {e}")
            log.warning("voice: playback failed: %s", e)
            self._speaking.clear()
            return False

    def _fallback_speak(self, sentence):
        if self.on_speech_scheduled:
            try:
                self.on_speech_scheduled(sentence)
            except Exception:
                pass
        try:
            import pyttsx3

            engine = pyttsx3.init("sapi5")
            self._speaking.set()
            log.info("voice: SAPI fallback speaking")
            try:
                engine.setProperty("rate", 200)
                engine.say(sentence)
                engine.runAndWait()
            finally:
                self._speaking.clear()
        except Exception as e:
            print(f"[voice] fallback speak failed: {e}")
            log.warning("voice: fallback speak failed: %s", e)
